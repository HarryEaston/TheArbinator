"""Monitor loop for the auto-hedger.

Drives one parlay from confirmation through completion:

1. Place hedge 1 on Polymarket immediately.
2. Watch leg 1's resolution (Polymarket hedge-token price).
3. If leg 1 won, place hedge 2; if leg 1 lost, stop (hedge 1 already paid).
4. Repeat until the last leg wins (parlay hits) or any leg loses (parlay dead).

State is persisted to ``state/`` after every transition, so ``auto --resume``
picks up exactly where it left off. The per-leg ``order_id`` ledger guarantees a
hedge is never placed twice.
"""

from __future__ import annotations

from datetime import datetime, timezone

from bonusarb.auto.config import AutoConfig
from bonusarb.auto.executor import (
    FAILED,
    FILLED,
    PARTIAL,
    SKIPPED,
    ClobExecutor,
    FillResult,
    HedgeContext,
)
from bonusarb.auto.notify import TelegramNotifier
from bonusarb.auto.state import (
    LEG_HEDGE_PLACED,
    LEG_LOST,
    LEG_PENDING,
    LEG_WON,
    ActiveParlay,
    LegState,
)
from bonusarb.auto.watcher import LOST, PENDING, ResolutionWatcher, WON
from bonusarb.fx import format_sportsbook_amount, format_usd_amount


def run_monitor(
    parlay: ActiveParlay,
    executor: ClobExecutor,
    watcher: ResolutionWatcher,
    notifier: TelegramNotifier,
    config: AutoConfig,
) -> ActiveParlay:
    parlay.status = "active"
    parlay.save()

    while True:
        leg = _current_leg(parlay)

        if leg is None:
            _finalize_all_won(parlay, notifier)
            return parlay

        if leg.status == LEG_PENDING:
            _place_hedge(parlay, leg, executor, notifier, config)
            continue  # re-evaluate; leg is now HEDGE_PLACED (or paused)

        if leg.status == LEG_HEDGE_PLACED:
            resolved = _watch_leg(parlay, leg, watcher, notifier, config)
            if not resolved:
                # Still pending; sleep handled by caller. Pause to avoid busy loop.
                return parlay
            if leg.status == LEG_LOST:
                _finalize_dead(parlay, leg, notifier)
                return parlay
            # WON -> loop continues to the next pending leg.
            continue


def _current_leg(parlay: ActiveParlay) -> LegState | None:
    """The active leg: the earliest not-yet-resolved leg."""
    for leg in parlay.legs:
        if leg.status in {LEG_PENDING, LEG_HEDGE_PLACED}:
            return leg
    return None


def _place_hedge(
    parlay: ActiveParlay,
    leg: LegState,
    executor: ClobExecutor,
    notifier: TelegramNotifier,
    config: AutoConfig,
) -> None:
    # Idempotency: never place twice. The caller only calls this for PENDING
    # legs, but a resume after a write race would find an order_id already set.
    if leg.order_id is not None:
        leg.status = LEG_HEDGE_PLACED
        parlay.save()
        return

    context = _hedge_context(parlay, leg)
    result = executor.place_hedge(leg, context)

    if result.status in {FILLED, PARTIAL}:
        leg.order_id = result.order_id
        leg.filled_shares = result.filled_shares
        leg.filled_price = result.filled_price
        leg.filled_cost = result.filled_cost
        leg.status = LEG_HEDGE_PLACED
        parlay.save()
        notifier.hedge_placed(
            f"Leg {leg.leg_index}: {leg.event_label} -> {leg.selection}",
            shares=result.filled_shares,
            price=result.filled_price,
            cost=result.filled_cost,
        )
        if result.status == PARTIAL:
            notifier.alert(
                f"Leg {leg.leg_index} hedge PARTIALLY filled "
                f"({result.filled_shares:.2f} shares @ {result.filled_price:.4f}). "
                f"Locked profit estimate "
                f"{format_sportsbook_amount(result.locked_profit_after, parlay.usd_cad_rate)}. "
                f"Review manually."
            )
        return

    # FAILED or SKIPPED -> pause and alert; do not advance.
    leg.status = LEG_PENDING  # stay pending so resume retries
    parlay.status = "paused"
    parlay.save()
    notifier.alert(
        f"Leg {leg.leg_index} hedge NOT placed ({result.status}). "
        f"{result.reason or ''} "
        f"Live price={result.live_price:.4f}, planned stake=${leg.planned_stake:.2f}. "
        f"Paused -- resolve and re-run `auto --resume`."
    )


def _watch_leg(
    parlay: ActiveParlay,
    leg: LegState,
    watcher: ResolutionWatcher,
    notifier: TelegramNotifier,
    config: AutoConfig,
) -> bool:
    resolution = watcher.resolve(leg.hedge_token_id)
    if resolution.status == PENDING:
        return False

    won = resolution.status == WON
    leg.resolved_won = won
    leg.resolved_at_iso = datetime.now(timezone.utc).isoformat()
    leg.status = LEG_WON if won else LEG_LOST
    parlay.save()
    notifier.leg_resolved(
        f"Leg {leg.leg_index}: {leg.event_label}",
        won=won,
    )
    return True


def _hedge_context(parlay: ActiveParlay, leg: LegState) -> HedgeContext:
    # Already-placed hedges are the legs before this one with a recorded fill.
    already_placed: list[float] = []
    remaining_odds: list[float] = []
    seen_current = False
    for other in parlay.legs:
        if other.leg_index == leg.leg_index:
            seen_current = True
            remaining_odds.append(other.planned_odds)
            continue
        if not seen_current:
            if other.filled_cost is not None:
                already_placed.append(other.filled_cost)
            # Skipped/lost prior legs shouldn't happen here (we stop on a loss),
            # but if a prior leg has no fill, treat its planned stake as sunk.
            elif other.status in {LEG_WON}:
                already_placed.append(other.planned_stake)
        else:
            remaining_odds.append(other.planned_odds)

    return HedgeContext(
        stake_cost=parlay.stake_cost,
        win_profit=parlay.win_profit,
        already_placed_stakes=already_placed,
        remaining_planned_odds=remaining_odds,
    )


def _finalize_all_won(parlay: ActiveParlay, notifier: TelegramNotifier) -> None:
    hedge_total = sum(leg.filled_cost or 0.0 for leg in parlay.legs)
    net = parlay.win_profit - hedge_total
    rate = parlay.usd_cad_rate
    parlay.status = "complete"
    parlay.summary = (
        f"All {len(parlay.legs)} legs won. "
        f"Win profit {format_sportsbook_amount(parlay.win_profit, rate)} - "
        f"hedges {format_usd_amount(hedge_total)} "
        f"= net {format_sportsbook_amount(net, rate)}."
    )
    parlay.save()
    notifier.complete(parlay.summary)


def _finalize_dead(parlay: ActiveParlay, lost_leg: LegState, notifier: TelegramNotifier) -> None:
    prior_hedges = sum(
        leg.filled_cost or 0.0 for leg in parlay.legs if leg.leg_index < lost_leg.leg_index
    )
    dead_hedge_payout = (lost_leg.filled_shares or 0.0) * 1.0  # hedge shares pay $1 each
    dead_hedge_cost = lost_leg.filled_cost or 0.0
    net = dead_hedge_payout - dead_hedge_cost - prior_hedges - parlay.stake_cost
    rate = parlay.usd_cad_rate
    parlay.status = "complete"
    parlay.summary = (
        f"Leg {lost_leg.leg_index} ({lost_leg.event_label}) lost. Parlay dead. "
        f"Hedge paid {format_usd_amount(dead_hedge_payout)} "
        f"(cost {format_usd_amount(dead_hedge_cost)}); "
        f"prior hedges {format_usd_amount(prior_hedges)}; "
        f"sportsbook stake {format_sportsbook_amount(parlay.stake_cost, rate)}. "
        f"Net {format_sportsbook_amount(net, rate)}."
    )
    parlay.save()
    notifier.complete(parlay.summary)
