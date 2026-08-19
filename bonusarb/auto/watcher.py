"""Live resolution watcher for auto-hedged parlay legs.

We watch the *hedge* token (the Polymarket outcome we bought shares of, which is
the opposite of the token-side selection). Its share price pins the leg state:

- hedge price -> 1.0  => opposite won => token-side selection LOST => parlay dead
- hedge price -> 0.0  => opposite lost => token-side selection WON  => parlay alive

Asymmetric thresholds load risk onto the safer direction:

- **lost_threshold** (default 0.97): declare LOST when hedge price >= this.
  Wrong stop only misses later hedges (forgone profit).
- **won_threshold** (default 0.99): declare WON when hedge price <= 1 - this
  (i.e. <= 0.01). Wrong advance places an unhedged next-leg bet, so we demand
  higher certainty before placing the next hedge.

A single extreme quote is not enough: ``confirm_polls`` consecutive agreeing
classifications are required (default 3). If Gamma already marks the market
closed with a final outcome, that accelerates the decision — it is never
required, so delayed settlement cannot block the next hedge.
"""

from __future__ import annotations

from dataclasses import dataclass

from bonusarb.polymarket.client import PolymarketClient


PENDING = "pending"
WON = "won"
LOST = "lost"


@dataclass(frozen=True)
class LegResolution:
    status: str  # PENDING | WON | LOST
    hedge_price: float | None
    confirm_count: int = 0
    source: str = "price"  # "price" | "gamma"


class ResolutionWatcher:
    def __init__(
        self,
        client: PolymarketClient,
        *,
        won_threshold: float = 0.99,
        lost_threshold: float = 0.97,
        confirm_polls: int = 3,
    ) -> None:
        self.client = client
        self.won_threshold = won_threshold
        self.lost_threshold = lost_threshold
        self.confirm_polls = max(1, int(confirm_polls))
        # token_id -> (candidate status, consecutive count)
        self._streaks: dict[str, tuple[str, int]] = {}

    def resolve(
        self,
        hedge_token_id: str | None,
        *,
        event_slug: str | None = None,
    ) -> LegResolution:
        if not hedge_token_id:
            return LegResolution(PENDING, None)

        # Gamma closed can only accelerate — never delay past the price path.
        gamma = self.client.get_token_resolution(
            hedge_token_id,
            event_slug=event_slug,
            force_fetch=True,
        )
        if gamma in {WON, LOST}:
            self._streaks.pop(hedge_token_id, None)
            return LegResolution(gamma, None, confirm_count=self.confirm_polls, source="gamma")

        price = self.client.get_token_price(hedge_token_id, side="BUY", force_fetch=True)
        if price is None:
            return LegResolution(PENDING, None)

        candidate = self._classify_once(price)
        if candidate == PENDING:
            self._streaks.pop(hedge_token_id, None)
            return LegResolution(PENDING, price, confirm_count=0, source="price")

        prev_status, prev_count = self._streaks.get(hedge_token_id, (PENDING, 0))
        if prev_status == candidate:
            count = prev_count + 1
        else:
            count = 1
        self._streaks[hedge_token_id] = (candidate, count)

        if count >= self.confirm_polls:
            self._streaks.pop(hedge_token_id, None)
            return LegResolution(candidate, price, confirm_count=count, source="price")
        return LegResolution(PENDING, price, confirm_count=count, source="price")

    def _classify_once(self, hedge_price: float) -> str:
        if hedge_price >= self.lost_threshold:
            return LOST
        if hedge_price <= 1.0 - self.won_threshold:
            return WON
        return PENDING
