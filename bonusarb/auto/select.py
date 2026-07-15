"""Plan selection for the auto-hedger.

Reuses the existing scan pipeline (``runners.scan_plans``) so the ``auto``
command sees the exact same parlays as ``run_auto``, then keeps only plans whose
every hedge is placeable on Polymarket (the only venue we can auto-trade),
renders them, and prompts the user to pick one and confirm the sportsbook bet
has been placed by hand.
"""

from __future__ import annotations

from bonusarb.display import format_display_time, format_event_cell
from bonusarb.fx import format_sportsbook_amount, format_usd_amount
from bonusarb.oddsapi.cache import OddsCache
from bonusarb.oddsapi.client import OddsApiClient
from bonusarb.report import render_report
from bonusarb.runners import RunConfig, scan_plans


def is_automatable(plan: HedgePlan) -> bool:
    """True when every hedge in the plan is on Polymarket with a token id."""
    if not plan.hedge_steps:
        return False
    for step in plan.hedge_steps:
        if step.book != "polymarket":
            return False
        if not step.hedge_token_id:
            return False
    return True


def filter_automatable(plans: list[HedgePlan]) -> list[HedgePlan]:
    return [plan for plan in plans if is_automatable(plan)]


def scan_automatable_plans(
    config: RunConfig, client: OddsApiClient, cache: OddsCache
) -> tuple[list[HedgePlan], list[str]]:
    """Run the scan and return only the fully Polymarket-hedgeable plans."""
    plans, _games, warnings = scan_plans(config, client, cache)
    return filter_automatable(plans), warnings


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
            "Auto-hedging only works when every leg's best hedge is on Polymarket."
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
    print("Automatable parlays (all hedges on Polymarket):")
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


def confirm_sportsbook_bet_placed(
    plan: HedgePlan,
    *,
    cad_rate: float | None = None,
) -> bool:
    print()
    print("Place this parlay on the sportsbook now:")
    print(f"  Book: {plan.token.token_book}")
    print(f"  Stake: {format_sportsbook_amount(plan.stake, cad_rate)}")
    for index, leg in enumerate(plan.legs, start=1):
        print(
            f"  Leg {index}: {format_event_cell(leg.event_label, leg.commence_time, plain=True)} "
            f"-> {leg.selection} @ {leg.token_odds:.2f}"
        )
    print()
    while True:
        raw = input("Have you placed the sportsbook bet? (y/n): ").strip().lower()
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Enter y or n.")
