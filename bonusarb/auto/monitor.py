"""Monitor loop for the auto-hedger.

Drives one parlay from confirmation through completion:

1. Place hedge 1 on Polymarket immediately.
2. Watch leg 1's resolution (Polymarket hedge-token price + optional Gamma).
3. If leg 1 won, place hedge 2; if leg 1 lost, stop (hedge 1 already paid).
4. Repeat until the last leg wins (parlay hits) or any leg loses (parlay dead).

State is persisted to ``state/`` after every transition, so ``auto --resume``
picks up exactly where it left off. The per-leg ``order_id`` / ``client_order_id``
ledger guarantees a hedge is never placed twice. Partial fills pause the
monitor instead of advancing under-hedged.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from bonusarb.auto.config import AutoConfig
from bonusarb.auto.executor import (
    FAILED,
    FILLED,
    PARTIAL,
    SKIPPED,
    ClobExecutor,
    HedgeContext,
)
from bonusarb.auto.notify import TelegramNotifier
from bonusarb.auto.state import (
    LEG_HEDGE_PLACED,
    LEG_LOST,
    LEG_PENDING,
    LEG_PLACING,
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
        if parlay.status == "paused":
            return parlay

        leg = _current_leg(parlay)

        if leg is None:
            _finalize_all_won(parlay, notifier)
            return parlay

        if leg.status in {LEG_PENDING, LEG_PLACING}:
            _place_hedge(parlay, leg, executor, notifier, config)
            continue  # re-evaluate; leg is now HEDGE_PLACED (or paused)

        if leg.status == LEG_HEDGE_PLACED:
            resolved = _watch_leg(parlay, leg, watcher, notifier, config)
            if not resolved:
                # Still pending; sleep handled by caller. Return to avoid busy loop.
                return parlay
            if leg.status == LEG_LOST:
                _finalize_dead(parlay, leg, notifier)
                return parlay
            # WON -> loop continues to the next pending leg.
            continue

    return parlay


def _current_leg(parlay: ActiveParlay) -> LegState | None:
    """The active leg: the earliest not-yet-resolved leg."""
    for leg in parlay.legs:
        if leg.status in {LEG_PENDING, LEG_PLACING, LEG_HEDGE_PLACED}:
            return leg
    return None


def _place_hedge(
    parlay: ActiveParlay,
    leg: LegState,
    executor: ClobExecutor,
    notifier: TelegramNotifier,
    config: AutoConfig,
) -> None:
    # Idempotency: a confirmed full fill must never be re-posted.
    if leg.order_id is not None and leg.status == LEG_HEDGE_PLACED:
        return

    # Partial fill left order_id on a PENDING leg — do not treat as complete and
    # do not silently re-post; operator must resolve then resume.
    if leg.order_id is not None and leg.status == LEG_PENDING:
        parlay.status = "paused"
        parlay.save()
        notifier.alert(
            f"Leg {leg.leg_index} has a recorded order ({leg.order_id}) but is not "
            f"fully hedged (status={leg.status}, shares={leg.filled_shares}). "
            f"Paused -- top up or reconcile manually, then `auto --resume`."
        )
        return

    # Crash mid-flight: reconcile before posting again.
    if leg.status == LEG_PLACING or (
        leg.client_order_id is not None and leg.order_id is None and leg.status == LEG_PENDING
    ):
        if _reconcile_leg(parlay, leg, executor, notifier):
            return
        # Still unresolved after reconcile — stay paused rather than double-post.
        if leg.status == LEG_PLACING:
            parlay.status = "paused"
            parlay.save()
            notifier.alert(
                f"Leg {leg.leg_index} was mid-placement (client_order_id="
                f"{leg.client_order_id}). Could not confirm fill. Paused -- "
                f"check the CLOB then `auto --resume`."
            )
            return

    # Pre-submit intent: persist before touching the CLOB.
    if not leg.client_order_id:
        leg.client_order_id = f"ba-{uuid.uuid4().hex}"
    leg.status = LEG_PLACING
    parlay.save()

    context = _hedge_context(parlay, leg)
    result = executor.place_hedge(leg, context)

    if result.status == FILLED:
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
        if result.locked_profit_after < config.min_locked_profit_floor:
            parlay.status = "paused"
            parlay.save()
            notifier.alert(
                f"Leg {leg.leg_index} filled but post-fill locked profit "
                f"${result.locked_profit_after:.2f} is below floor "
                f"${config.min_locked_profit_floor:.2f}. Paused for review."
            )
        return

    if result.status == PARTIAL:
        # Record what filled, stay pending, pause — do not advance under-hedged.
        leg.order_id = result.order_id
        leg.filled_shares = result.filled_shares
        leg.filled_price = result.filled_price
        leg.filled_cost = result.filled_cost
        leg.status = LEG_PENDING
        parlay.status = "paused"
        parlay.save()
        notifier.alert(
            f"Leg {leg.leg_index} hedge PARTIALLY filled "
            f"({result.filled_shares:.2f} shares @ {result.filled_price:.4f}). "
            f"Locked profit estimate "
            f"{format_sportsbook_amount(result.locked_profit_after, parlay.usd_cad_rate)}. "
            f"Paused -- top up or resolve manually, then `auto --resume`."
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


def _reconcile_leg(
    parlay: ActiveParlay,
    leg: LegState,
    executor: ClobExecutor,
    notifier: TelegramNotifier,
) -> bool:
    """Try to adopt an existing CLOB fill for a mid-flight leg. Returns True if done."""
    if executor.paper:
        # Paper never leaves a real exchange order; clear placing and retry.
        leg.status = LEG_PENDING
        parlay.save()
        return False

    if not leg.order_id:
        # Without an exchange order id we cannot safely claim a fill.
        return False

    status = executor.order_api.get_order_status(leg.order_id)
    if status.filled_shares <= 0.0:
        return False

    planned_shares = None
    if leg.planned_stake and leg.filled_price:
        planned_shares = leg.planned_stake / max(leg.filled_price, 1e-9)

    leg.filled_shares = status.filled_shares
    leg.filled_price = status.avg_price or leg.filled_price or 0.0
    from bonusarb.polymarket.client import all_in_buy_cost

    leg.filled_cost = all_in_buy_cost(
        status.filled_shares, status.avg_price or leg.filled_price or 0.0
    )

    full = planned_shares is None or status.filled_shares >= planned_shares - 1e-6
    if full or status.status in {"matched", "filled"}:
        # Treat matched as complete when we have any positive fill and no planned size.
        if planned_shares is None or status.filled_shares >= planned_shares - 1e-6:
            leg.status = LEG_HEDGE_PLACED
            parlay.save()
            notifier.hedge_placed(
                f"Leg {leg.leg_index}: {leg.event_label} -> {leg.selection} (reconciled)",
                shares=leg.filled_shares or 0.0,
                price=leg.filled_price or 0.0,
                cost=leg.filled_cost or 0.0,
            )
            return True

    leg.status = LEG_PENDING
    parlay.status = "paused"
    parlay.save()
    notifier.alert(
        f"Leg {leg.leg_index} reconcile found partial fill on {leg.order_id} "
        f"({status.filled_shares:.2f} shares). Paused for manual review."
    )
    return True


def _watch_leg(
    parlay: ActiveParlay,
    leg: LegState,
    watcher: ResolutionWatcher,
    notifier: TelegramNotifier,
    config: AutoConfig,
) -> bool:
    resolution = watcher.resolve(
        leg.hedge_token_id,
        event_slug=leg.polymarket_event_slug,
    )
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
