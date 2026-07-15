"""Resumable, idempotent state for an in-progress auto-hedged parlay.

A single ``ActiveParlay`` is persisted as JSON under ``state/``. The monitor
loop re-reads this after every step so a crash/restart (``auto --resume``) can
pick up exactly where it left off without re-placing a hedge. The per-leg
``order_id`` ledger is the idempotency key: a hedge is only placed when its
leg has no recorded order id, and resolution is only advanced for legs whose
hedge is already on the books.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from bonusarb.config import STATE_DIR
from bonusarb.models import HedgePlan, HedgeStep, Leg


LEG_PENDING = "pending"
LEG_HEDGE_PLACED = "hedge_placed"
LEG_WON = "won"
LEG_LOST = "lost"


@dataclass
class LegState:
    leg_index: int
    event_label: str
    selection: str
    commence_time_iso: str
    place_by_iso: str
    market_key: str
    polymarket_event_slug: str | None
    hedge_token_id: str | None
    planned_stake: float
    planned_odds: float
    status: str = LEG_PENDING
    # Filled order details (None until the hedge is placed).
    order_id: str | None = None
    filled_shares: float | None = None
    filled_price: float | None = None
    filled_cost: float | None = None
    # Outcome of the underlying leg (set when the watcher resolves it).
    resolved_won: bool | None = None
    resolved_at_iso: str | None = None


@dataclass
class ActiveParlay:
    id: str
    created_at_iso: str
    sport_key: str
    token_book: str
    stake: float
    combined_odds: float
    stake_cost: float
    win_profit: float
    locked_profit_plan: float
    legs: list[LegState] = field(default_factory=list)
    status: str = "active"  # active | complete | paused
    summary: str = ""
    # CAD per USD at run start; used for display on resume (stakes sized in USD).
    usd_cad_rate: float | None = None

    @property
    def path(self) -> Path:
        return STATE_DIR / f"parlay_{self.id}.json"

    @classmethod
    def from_plan(
        cls,
        plan: HedgePlan,
        sport_key: str,
        *,
        usd_cad_rate: float | None = None,
    ) -> "ActiveParlay":
        # A uuid suffix keeps ids unique even when two parlays are created in
        # the same wall-clock second (e.g. running `auto` in two terminals),
        # which would otherwise collide on the same state file.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        parlay_id = f"{timestamp}_{uuid4().hex[:6]}Z"
        legs = [_leg_state_from_step(step, index, plan) for index, step in enumerate(plan.hedge_steps)]
        return cls(
            id=parlay_id,
            created_at_iso=datetime.now(timezone.utc).isoformat(),
            sport_key=sport_key,
            token_book=plan.token.token_book,
            stake=plan.stake,
            combined_odds=plan.combined_odds,
            stake_cost=plan.stake_cost,
            win_profit=plan.effective_win_profit,
            locked_profit_plan=plan.locked_profit,
            legs=legs,
            usd_cad_rate=usd_cad_rate,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=_json_default)

    def save(self) -> Path:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        path = self.path
        path.write_text(self.to_json(), encoding="utf-8")
        return path

    def next_pending_leg(self) -> LegState | None:
        """The earliest leg whose hedge has not been placed yet."""
        for leg in self.legs:
            if leg.status == LEG_PENDING:
                return leg
        return None

    def placed_legs(self) -> list[LegState]:
        return [leg for leg in self.legs if leg.order_id is not None]

    def all_resolved(self) -> bool:
        return all(leg.status in {LEG_WON, LEG_LOST} for leg in self.legs)


def _leg_state_from_step(step: HedgeStep, index: int, plan: HedgePlan) -> LegState:
    # HedgeStep.selection is a display string (e.g. "Team X -3.5"); carry it for
    # logs. The CLOB token id is the authoritative handle for placing/watching.
    return LegState(
        leg_index=step.leg_index,
        event_label=step.event_label,
        selection=step.selection,
        commence_time_iso=_iso(step.commence_time),
        place_by_iso=_iso(step.place_by),
        market_key=step.market_key,
        polymarket_event_slug=step.polymarket_event_slug,
        hedge_token_id=step.hedge_token_id,
        planned_stake=step.stake,
        planned_odds=step.odds,
    )


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _json_default(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return _iso(obj)
    raise TypeError(f"Not serializable: {type(obj)}")


def load_parlay(path: Path) -> ActiveParlay:
    data = json.loads(path.read_text(encoding="utf-8"))
    legs = [LegState(**leg) for leg in data.pop("legs", [])]
    return ActiveParlay(**data, legs=legs)


def all_parlay_paths() -> list[Path]:
    """Every saved parlay state file, oldest first."""
    if not STATE_DIR.exists():
        return []
    return sorted(STATE_DIR.glob("parlay_*.json"))


def latest_parlay_path() -> Path | None:
    files = all_parlay_paths()
    return files[-1] if files else None


def parlay_path_for_id(parlay_id: str) -> Path:
    return STATE_DIR / f"parlay_{parlay_id}.json"


def list_in_progress_parlays() -> list[ActiveParlay]:
    """Load every saved parlay that hasn't finished (status "active" or "paused").

    Lets you run several ``auto`` processes at once (one per terminal) and see
    which parlays are still live without guessing ids.
    """
    parlays = [load_parlay(path) for path in all_parlay_paths()]
    return [parlay for parlay in parlays if parlay.status in {"active", "paused"}]


def leg_from_state(leg_state: LegState) -> Leg:
    """Reconstruct a minimal Leg for schedule/finish helpers."""
    return Leg(
        game_id="",
        event_label=leg_state.event_label,
        commence_time=datetime.fromisoformat(leg_state.commence_time_iso),
        selection="",
        opposite_selection=leg_state.selection,
        token_book="fanduel",  # unused for finish-time math
        token_odds=0.0,
        hedge_book="polymarket",
        hedge_odds=leg_state.planned_odds,
        market_key=leg_state.market_key,
        polymarket_event_slug=leg_state.polymarket_event_slug,
        hedge_token_id=leg_state.hedge_token_id,
    )


# Monotonic-ish epoch for cache-busting price reads during tests.
def now_epoch() -> float:
    return time.time()
