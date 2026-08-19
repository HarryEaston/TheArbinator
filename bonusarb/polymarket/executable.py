"""Force-reprice Polymarket hedges from live CLOB depth (VWAP).

Actionable / auto plans must not display locked profit from stale cache or
Gamma fallback prices. Before display (and again before confirm), each
Polymarket hedge is re-quoted from the CLOB book at the planned share size.
"""

from __future__ import annotations

from dataclasses import replace

from bonusarb.arb.sequential import solve_sequential_hedge
from bonusarb.config import AUTO_MAX_SLIPPAGE, DEFAULT_MIN_GAP_MINUTES
from bonusarb.models import HedgePlan, Leg
from bonusarb.polymarket.client import (
    PRICE_SOURCE_CLOB,
    PolymarketClient,
    share_price_to_decimal_odds,
)


def reprice_plan_executable(
    plan: HedgePlan,
    client: PolymarketClient,
    *,
    sport_key: str,
    max_slippage: float = AUTO_MAX_SLIPPAGE,
    min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES,
) -> HedgePlan | None:
    """Return a plan sized from executable CLOB VWAP, or None if not tradeable."""
    if not plan.hedge_steps:
        return None

    # Pass 1: top-of-book / VWAP at currently planned sizes.
    updated_legs: list[Leg] = []
    for leg, step in zip(plan.legs, plan.hedge_steps):
        if leg.hedge_book != "polymarket" or not step.hedge_token_id:
            updated_legs.append(leg)
            continue
        shares = step.stake / max(1.0 / step.odds, 1e-9)
        # Prefer book VWAP; fall back to forcing a fresh top-of-book quote.
        vwap_result = client.executable_buy_for_shares(
            step.hedge_token_id,
            max(shares, 1.0),
            max_slippage=max_slippage,
            force_fetch=True,
        )
        if vwap_result is None:
            tob = client.get_token_price(step.hedge_token_id, side="BUY", force_fetch=True)
            if tob is None or not 0.0 < tob < 1.0:
                return None
            live_odds = share_price_to_decimal_odds(tob)
        else:
            live_odds = share_price_to_decimal_odds(vwap_result.vwap)
        updated_legs.append(
            replace(
                leg,
                hedge_odds=live_odds,
                hedge_price_source=PRICE_SOURCE_CLOB,
            )
        )

    rebuilt = solve_sequential_hedge(
        tuple(updated_legs),
        plan.token,
        plan.stake,
        sport_key,
        min_gap_minutes=min_gap_minutes,
    )
    if rebuilt is None or rebuilt.locked_profit <= 0:
        return None

    # Pass 2: confirm depth at the re-solved stake sizes.
    for step in rebuilt.hedge_steps:
        if step.book != "polymarket" or not step.hedge_token_id:
            continue
        # Invert post-fee odds to an approximate raw price for share count.
        # effective_taker_share_price(p) = 1/odds => solve share size from stake.
        p_eff = 1.0 / step.odds
        shares = step.stake / max(p_eff, 1e-9)
        check = client.executable_buy_for_shares(
            step.hedge_token_id,
            shares,
            max_slippage=max_slippage,
            force_fetch=True,
        )
        if check is None:
            return None

    # Mark all polymarket steps as clob-sourced after successful reprice.
    marked_steps = tuple(
        replace(step, hedge_price_source=PRICE_SOURCE_CLOB)
        if step.book == "polymarket"
        else step
        for step in rebuilt.hedge_steps
    )
    marked_legs = tuple(
        replace(leg, hedge_price_source=PRICE_SOURCE_CLOB)
        if leg.hedge_book == "polymarket"
        else leg
        for leg in rebuilt.legs
    )
    return replace(rebuilt, legs=marked_legs, hedge_steps=marked_steps)


def reprice_plans_executable(
    plans: list[HedgePlan],
    client: PolymarketClient,
    *,
    sport_key: str,
    max_slippage: float = AUTO_MAX_SLIPPAGE,
) -> tuple[list[HedgePlan], int]:
    """Reprice each plan; return (survivors, dropped_count)."""
    kept: list[HedgePlan] = []
    dropped = 0
    for plan in plans:
        priced = reprice_plan_executable(
            plan, client, sport_key=sport_key, max_slippage=max_slippage
        )
        if priced is None:
            dropped += 1
            continue
        kept.append(priced)
    kept.sort(key=lambda p: p.locked_profit, reverse=True)
    return kept, dropped
