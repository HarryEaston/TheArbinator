"""Plan selection for the auto-hedger.

Reuses the existing scan pipeline (``runners.scan_plans``) so the ``auto``
command sees the exact same parlays as ``run_auto``, then keeps only plans whose
every hedge is placeable on Polymarket (the only venue we can auto-trade),
force-reprices from CLOB depth, renders them, and prompts the user to pick one
and reconcile the accepted sportsbook slip before hedging.
"""

from __future__ import annotations

from dataclasses import replace

from bonusarb.config import AUTO_MAX_SLIPPAGE, AUTO_MIN_LOCKED_PROFIT_FLOOR
from bonusarb.display import format_display_time, format_event_cell
from bonusarb.fx import format_sportsbook_amount
from bonusarb.models import HedgePlan, QuotaInfo, TokenConstraint
from bonusarb.oddsapi.cache import OddsCache
from bonusarb.oddsapi.client import OddsApiClient
from bonusarb.polymarket.client import PolymarketClient
from bonusarb.polymarket.executable import reprice_plans_executable
from bonusarb.report import render_report
from bonusarb.runners import RunConfig, scan_plans
from bonusarb.settlement import plan_settlement_ok
from bonusarb.tokens import token_payoff


def is_automatable(plan: HedgePlan, *, sport_key: str | None = None) -> bool:
    """True when every hedge is Polymarket+CLOB-sourced and settlement is safe."""
    if not plan.hedge_steps:
        return False
    ok, _reason = plan_settlement_ok(plan, sport_key=sport_key)
    if not ok:
        return False
    for step in plan.hedge_steps:
        if step.book != "polymarket":
            return False
        if not step.hedge_token_id:
            return False
        if step.hedge_price_source == "gamma_fallback":
            return False
    for leg in plan.legs:
        if leg.hedge_price_source == "gamma_fallback":
            return False
    return True


def filter_automatable(
    plans: list[HedgePlan],
    *,
    sport_key: str | None = None,
) -> list[HedgePlan]:
    return [plan for plan in plans if is_automatable(plan, sport_key=sport_key)]


def scan_automatable_plans(
    config: RunConfig,
    client: OddsApiClient,
    cache: OddsCache,
    *,
    polymarket_client: PolymarketClient | None = None,
    max_slippage: float = AUTO_MAX_SLIPPAGE,
) -> tuple[list[HedgePlan], list[str]]:
    """Run the scan, keep automatable plans, and force-reprice from CLOB depth."""
    plans, _games, warnings = scan_plans(config, client, cache)
    warnings = list(warnings)

    poly_complete = [
        p
        for p in plans
        if p.hedge_steps
        and all(s.book == "polymarket" and s.hedge_token_id for s in p.hedge_steps)
    ]
    settlement_drops = sum(
        1 for p in poly_complete if not plan_settlement_ok(p, sport_key=config.sport_key)[0]
    )
    if settlement_drops:
        warnings.append(
            f"{settlement_drops} Polymarket plan(s) excluded for unmodeled "
            "settlement risk (NFL/UFC ties-draws-NC, or non-h2h markets)."
        )

    automatable = filter_automatable(plans, sport_key=config.sport_key)
    poly = polymarket_client or PolymarketClient(cache=cache, dry_run=config.dry_run)
    priced, dropped = reprice_plans_executable(
        automatable,
        poly,
        sport_key=config.sport_key,
        max_slippage=max_slippage,
    )
    if dropped:
        warnings.append(
            f"{dropped} plan(s) dropped after live CLOB reprice / depth check "
            "(no executable VWAP within slippage)."
        )
    return priced, warnings


def render_automatable(
    plans: list[HedgePlan],
    token: TokenConstraint,
    quota: QuotaInfo,
    warnings: list[str],
    *,
    cad_rate: float | None = None,
) -> None:
    if not plans:
        render_report([], token, quota, warnings=warnings + [
            "No fully Polymarket-hedgeable parlays for the current settings. "
            "Auto-hedging only works when every leg's best hedge is on Polymarket "
            "with a live CLOB quote and executable depth."
        ], cad_rate=cad_rate)
        return
    render_report(plans, token, quota, warnings=warnings, cad_rate=cad_rate)


def prompt_pick_plan(
    plans: list[HedgePlan],
    *,
    cad_rate: float | None = None,
) -> HedgePlan | None:
    if not plans:
        print("No automatable plans to pick from.")
        return None
    print()
    print("Automatable parlays (all hedges on Polymarket, CLOB-repriced):")
    for index, plan in enumerate(plans, start=1):
        legs_summary = ", ".join(
            f"{leg.event_label} ({format_display_time(leg.commence_time)}): {leg.selection}"
            for leg in plan.legs
        )
        locked = format_sportsbook_amount(plan.locked_profit, cad_rate)
        stake = format_sportsbook_amount(plan.stake, cad_rate)
        print(
            f"  {index}. locked={locked}  "
            f"stake={stake}  ROI={plan.roi * 100:.1f}%  "
            f"legs: {legs_summary}"
        )
    while True:
        raw = input(f"Pick a plan [1-{len(plans)}] (or q to quit): ").strip().lower()
        if raw in {"q", "quit", ""}:
            return None
        if raw.isdigit() and 1 <= int(raw) <= len(plans):
            return plans[int(raw) - 1]
        print(f"Enter a number from 1 to {len(plans)}.")


def _prompt_float(prompt: str, default: float) -> float | None:
    while True:
        raw = input(f"{prompt} [{default:.4g}]: ").strip()
        if raw == "":
            return default
        if raw.lower() in {"q", "quit"}:
            return None
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            print("Enter a number (or q to quit).")
            continue
        if value < 0:
            print("Enter a non-negative number.")
            continue
        return value


def confirm_sportsbook_bet_placed(
    plan: HedgePlan,
    *,
    cad_rate: float | None = None,
    sport_key: str | None = None,
    min_locked_profit_floor: float = AUTO_MIN_LOCKED_PROFIT_FLOOR,
) -> HedgePlan | None:
    """Confirm placement and capture the accepted slip; return rebuilt plan or None."""
    print()
    print("Place this parlay on the sportsbook now:")
    print(f"  Book: {plan.token.token_book}")
    print(f"  Scanned stake: {format_sportsbook_amount(plan.stake, cad_rate)}")
    for index, leg in enumerate(plan.legs, start=1):
        print(
            f"  Leg {index}: {format_event_cell(leg.event_label, leg.commence_time, plain=True)} "
            f"-> {leg.selection} @ {leg.token_odds:.2f}"
        )
    print()
    while True:
        raw = input("Have you placed the sportsbook bet? (y/n): ").strip().lower()
        if raw in {"y", "yes"}:
            break
        if raw in {"n", "no"}:
            return None
        print("Enter y or n.")

    print()
    print("Reconcile the accepted slip (press Enter to keep the scanned default):")
    stake = _prompt_float("Accepted stake", plan.stake)
    if stake is None or stake <= 0:
        return None
    boost_pct = _prompt_float(
        "Accepted boost fraction (e.g. 0.30 for 30%)",
        plan.token.boost_pct,
    )
    if boost_pct is None:
        return None
    combined = _prompt_float("Accepted combined decimal odds", plan.combined_odds)
    if combined is None or combined <= 1.0:
        print("Combined odds must be greater than 1.")
        return None

    return rebuild_plan_from_accepted_slip(
        plan,
        accepted_stake=stake,
        accepted_boost_pct=boost_pct,
        accepted_combined_odds=combined,
        sport_key=sport_key,
        min_locked_profit_floor=min_locked_profit_floor,
    )


def rebuild_plan_from_accepted_slip(
    plan: HedgePlan,
    *,
    accepted_stake: float,
    accepted_boost_pct: float,
    accepted_combined_odds: float,
    sport_key: str | None = None,
    min_locked_profit_floor: float = AUTO_MIN_LOCKED_PROFIT_FLOOR,
) -> HedgePlan | None:
    """Rebuild hedge stakes from the sportsbook slip the user actually got."""
    from bonusarb.arb.sequential import recompute_remaining_hedges

    token = replace(plan.token, boost_pct=accepted_boost_pct)
    stake_cost, win_profit = token_payoff(token, accepted_stake, accepted_combined_odds)
    hedge_odds = [step.odds for step in plan.hedge_steps]
    if any(o <= 1.0 for o in hedge_odds):
        return None
    locked, hedge_stakes = recompute_remaining_hedges(
        stake_cost, win_profit, hedge_odds, []
    )
    if locked < min_locked_profit_floor:
        print(
            f"Accepted slip locks only ${locked:.2f}, below floor "
            f"${min_locked_profit_floor:.2f}. Aborting."
        )
        return None

    new_steps = tuple(
        replace(step, stake=hedge_stakes[i])
        for i, step in enumerate(plan.hedge_steps)
    )
    max_cash = stake_cost + sum(hedge_stakes)
    roi = locked / max_cash if max_cash > 0 else 0.0
    return replace(
        plan,
        token=token,
        stake=accepted_stake,
        combined_odds=accepted_combined_odds,
        stake_cost=stake_cost,
        effective_win_profit=win_profit,
        hedge_steps=new_steps,
        locked_profit=locked,
        max_cash_needed=max_cash,
        roi=roi,
    )
