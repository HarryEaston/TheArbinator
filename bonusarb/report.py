"""Rich terminal reporting."""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from bonusarb.display import format_display_time, format_event_cell, format_selection, market_label
from bonusarb.fx import (
    cad_to_usd,
    format_hedge_stake,
    format_sportsbook_amount,
    format_usd_amount,
)
from bonusarb.config import ARB_LEAGUES, LEAGUES
from bonusarb.models import HedgePlan, QuotaInfo, TokenConstraint, TokenType, TwoWayArb
from bonusarb.odds_utils import decimal_to_american, leg_selection_display
from bonusarb.tokens import effective_combined_odds

BOOK_LABELS = {
    "fanduel": "FanDuel",
    "draftkings": "DraftKings",
    "betmgm": "BetMGM",
    "espnbet": "theScore Bet",
    "polymarket": "Polymarket",
}


def render_report(
    plans: list[HedgePlan],
    token: TokenConstraint,
    quota: QuotaInfo,
    *,
    ev_plans: list[HedgePlan] | None = None,
    warnings: list[str] | None = None,
    cad_rate: float | None = None,
) -> None:
    console = Console()
    console.print()
    console.print(_token_panel(token, quota, warnings or [], cad_rate=cad_rate))

    if not plans:
        console.print("[yellow]No guaranteed-profit plans found for the current settings.[/yellow]")
        if ev_plans:
            console.print("[cyan]Best positive expected-value alternatives:[/cyan]")
            for index, plan in enumerate(ev_plans, start=1):
                console.print(_plan_panel(plan, title=f"+EV Option {index}", cad_rate=cad_rate))
        return

    for index, plan in enumerate(plans, start=1):
        console.print(_plan_panel(plan, title=f"Guaranteed Plan {index}", cad_rate=cad_rate))


def render_arb_report(
    arbs: list[TwoWayArb],
    quota: QuotaInfo,
    *,
    capital: float,
    sport_keys: tuple[str, ...],
    warnings: list[str] | None = None,
    cad_rate: float | None = None,
) -> None:
    console = Console()
    console.print()
    console.print(
        _arb_header_panel(
            quota,
            capital=capital,
            sport_keys=sport_keys,
            warnings=warnings or [],
            cad_rate=cad_rate,
        )
    )

    if not arbs:
        console.print("[yellow]No positive-edge 1-leg arbitrage found.[/yellow]")
        return

    for index, arb in enumerate(arbs, start=1):
        console.print(
            _arb_panel(arb, title=f"Arb #{index}  (ROI {arb.roi * 100:.2f}%)", cad_rate=cad_rate)
        )


def _league_names(sport_keys: tuple[str, ...]) -> str:
    names_by_key = {key: name for key, name in LEAGUES}
    names_by_key.update({key: name for key, name in ARB_LEAGUES})
    return ", ".join(names_by_key.get(key, key) for key in sport_keys)


def _arb_header_panel(
    quota: QuotaInfo,
    *,
    capital: float,
    sport_keys: tuple[str, ...],
    warnings: list[str],
    cad_rate: float | None,
) -> Panel:
    capital_label = (
        f"{capital:.2f} CAD"
        if cad_rate is not None and cad_rate > 0
        else f"{capital:.2f} USD"
    )
    lines = [
        f"Leagues: [bold]{_league_names(sport_keys)}[/bold]",
        f"Capital: [bold]{capital_label}[/bold] (split across both sides)",
        "Mode: 1-leg all-pairs (sportsbooks + Polymarket)",
    ]
    if quota.remaining is not None:
        lines.append(f"API credits remaining: {quota.remaining}")
    if quota.used is not None:
        lines.append(f"API credits used: {quota.used}")
    if quota.last_cost is not None:
        lines.append(f"Last request cost: {quota.last_cost}")
    for warning in warnings:
        lines.append(f"[yellow]Warning:[/yellow] {warning}")
    return Panel("\n".join(lines), title="General 1-Leg Arbitrage Finder", border_style="blue")


def _format_arb_stake(stake: float, book: str, cad_rate: float | None) -> str:
    """Format a stake: sportsbooks in capital currency; Polymarket in USD when CAD."""
    if book == "polymarket":
        if cad_rate is not None and cad_rate > 0:
            return format_usd_amount(cad_to_usd(stake, cad_rate))
        return format_usd_amount(stake)
    if cad_rate is not None and cad_rate > 0:
        return f"${stake:.2f} CAD"
    return f"${stake:.2f} USD"


def _arb_panel(arb: TwoWayArb, title: str, *, cad_rate: float | None = None) -> Panel:
    sides = Table(title="Place both sides now", show_header=True, header_style="bold")
    sides.add_column("Side", justify="right")
    sides.add_column("Book")
    sides.add_column("Market")
    sides.add_column("Selection")
    sides.add_column("Odds (Dec)")
    sides.add_column("Odds (Am)")
    sides.add_column("Stake")

    for label, book, selection, odds, point, stake in (
        ("A", arb.book_a, arb.selection_a, arb.odds_a, arb.point_a, arb.stake_a),
        ("B", arb.book_b, arb.selection_b, arb.odds_b, arb.point_b, arb.stake_b),
    ):
        book_label = BOOK_LABELS.get(book, book)
        if book == "polymarket":
            book_label = "Polymarket (buy shares)"
        sides.add_row(
            label,
            book_label,
            market_label(arb.market_key),
            format_selection(arb.market_key, selection, point),
            f"{odds:.2f}",
            str(decimal_to_american(odds)),
            _format_arb_stake(stake, book, cad_rate),
        )

    capital_label = (
        f"${arb.capital:.2f} CAD"
        if cad_rate is not None and cad_rate > 0
        else f"${arb.capital:.2f} USD"
    )
    profit_label = (
        f"${arb.locked_profit:.2f} CAD"
        if cad_rate is not None and cad_rate > 0
        else f"${arb.locked_profit:.2f} USD"
    )

    footer_lines = [
        f"Event: {format_event_cell(arb.event_label, arb.commence_time)}",
        f"Edge: {arb.edge * 100:.2f}%",
        f"Locked profit (worst path): [green]{profit_label}[/green]",
        f"Capital used: {capital_label}",
        f"ROI: [bold]{arb.roi * 100:.2f}%[/bold]",
        "Place both sides simultaneously (not sequential).",
    ]
    if arb.book_a == "polymarket" or arb.book_b == "polymarket":
        footer_lines.append(
            "[dim]Polymarket side is manual for now (auto-hedger wiring later).[/dim]"
        )

    content = Group(sides, "\n".join(footer_lines))
    return Panel(content, title=title, border_style="green")


def _token_panel(
    token: TokenConstraint,
    quota: QuotaInfo,
    warnings: list[str],
    *,
    cad_rate: float | None = None,
) -> Panel:
    stake_line = f"Max stake: {format_sportsbook_amount(token.max_stake, cad_rate)}"
    lines = [
        f"Token book: [bold]{token.token_book}[/bold]",
        f"Token type: [bold]{token.token_type.value}[/bold]",
        f"Min legs: {token.min_legs}",
        stake_line,
    ]
    if token.token_type == TokenType.PROFIT_BOOST:
        lines.append(f"Boost: {token.boost_pct * 100:.0f}%")

    if quota.remaining is not None:
        lines.append(f"API credits remaining: {quota.remaining}")
    if quota.used is not None:
        lines.append(f"API credits used: {quota.used}")
    if quota.last_cost is not None:
        lines.append(f"Last request cost: {quota.last_cost}")

    for warning in warnings:
        lines.append(f"[yellow]Warning:[/yellow] {warning}")

    return Panel("\n".join(lines), title="Bonus Token Arbitrage Finder", border_style="blue")


def _plan_panel(plan: HedgePlan, title: str, *, cad_rate: float | None = None) -> Panel:
    parlay_table = Table(title=f"Place on {plan.token.token_book}", show_header=True, header_style="bold")
    parlay_table.add_column("#", justify="right")
    parlay_table.add_column("Event")
    parlay_table.add_column("Market")
    parlay_table.add_column("Selection")
    parlay_table.add_column("Book Odds (Dec)")
    parlay_table.add_column("Book Odds (Am)")

    for index, leg in enumerate(plan.legs, start=1):
        parlay_table.add_row(
            str(index),
            format_event_cell(leg.event_label, leg.commence_time),
            market_label(leg.market_key),
            leg_selection_display(leg),
            f"{leg.token_odds:.2f}",
            str(decimal_to_american(leg.token_odds)),
        )

    parlay_table.add_row("", "", "", "Combined", f"{plan.combined_odds:.2f}", "")
    boosted_odds = effective_combined_odds(plan.token, plan.stake, plan.combined_odds)
    if abs(boosted_odds - plan.combined_odds) > 0.0001:
        parlay_table.add_row(
            "",
            "",
            "",
            "Effective w/Token",
            f"{boosted_odds:.2f}",
            str(decimal_to_american(boosted_odds)),
        )
    parlay_table.add_row("", "", "", "Stake", format_sportsbook_amount(plan.stake, cad_rate), "")

    hedge_table = Table(title="Hedge Schedule", show_header=True, header_style="bold")
    hedge_table.add_column("#", justify="right")
    hedge_table.add_column("Event")
    hedge_table.add_column("Market")
    hedge_table.add_column("Hedge")
    hedge_table.add_column("Book")
    hedge_table.add_column("Odds")
    hedge_table.add_column("Stake")
    hedge_table.add_column("Place By")

    for step in plan.hedge_steps:
        book_label = BOOK_LABELS.get(step.book, step.book)
        if step.book == "polymarket":
            book_label = "Polymarket (buy shares)"
        hedge_table.add_row(
            str(step.leg_index),
            step.event_label,
            market_label(step.market_key),
            step.selection,
            book_label,
            f"{step.odds:.2f}",
            format_hedge_stake(step.stake, step.book, cad_rate),
            format_display_time(step.place_by),
        )

    footer_lines = [
        f"Parlay win profit after token: {format_sportsbook_amount(plan.effective_win_profit, cad_rate)}",
        f"Locked profit: [green]{format_sportsbook_amount(plan.locked_profit, cad_rate)}[/green]",
        f"Max cash needed: {_format_max_cash_needed(plan, cad_rate)}",
        f"ROI: {plan.roi * 100:.2f}%",
        f"Guaranteed: {'Yes' if plan.is_guaranteed else 'No'}",
    ]
    for warning in plan.warnings:
        footer_lines.append(f"[yellow]{warning}[/yellow]")

    content = Group(parlay_table, hedge_table, "\n".join(footer_lines))
    return Panel(content, title=title, border_style="green" if plan.is_guaranteed else "yellow")


def _format_max_cash_needed(plan: HedgePlan, cad_rate: float | None) -> str:
    if cad_rate is None or cad_rate <= 0:
        return format_usd_amount(plan.max_cash_needed)
    poly_usd = sum(step.stake for step in plan.hedge_steps if step.book == "polymarket")
    sportsbook_hedges_usd = sum(
        step.stake for step in plan.hedge_steps if step.book != "polymarket"
    )
    parts = [f"{format_sportsbook_amount(plan.stake_cost, cad_rate)} (sportsbook)"]
    if poly_usd > 0:
        parts.append(f"{format_usd_amount(poly_usd)} (Polymarket)")
    if sportsbook_hedges_usd > 0:
        parts.append(
            f"{format_sportsbook_amount(sportsbook_hedges_usd, cad_rate)} (hedges)"
        )
    return " + ".join(parts)
