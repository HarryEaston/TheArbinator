"""Rich terminal reporting."""

from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from bonusarb.display import market_label
from bonusarb.models import HedgePlan, QuotaInfo, TokenConstraint, TokenType
from bonusarb.odds_utils import decimal_to_american, leg_selection_display
from bonusarb.tokens import effective_combined_odds

BOOK_LABELS = {
    "fanduel": "FanDuel",
    "draftkings": "DraftKings",
    "polymarket": "Polymarket",
}


def render_report(
    plans: list[HedgePlan],
    token: TokenConstraint,
    quota: QuotaInfo,
    *,
    ev_plans: list[HedgePlan] | None = None,
    warnings: list[str] | None = None,
) -> None:
    console = Console()
    console.print()
    console.print(_token_panel(token, quota, warnings or []))

    if not plans:
        console.print("[yellow]No guaranteed-profit plans found for the current settings.[/yellow]")
        if ev_plans:
            console.print("[cyan]Best positive expected-value alternatives:[/cyan]")
            for index, plan in enumerate(ev_plans, start=1):
                console.print(_plan_panel(plan, title=f"+EV Option {index}"))
        return

    for index, plan in enumerate(plans, start=1):
        console.print(_plan_panel(plan, title=f"Guaranteed Plan {index}"))


def _token_panel(token: TokenConstraint, quota: QuotaInfo, warnings: list[str]) -> Panel:
    lines = [
        f"Token book: [bold]{token.token_book}[/bold]",
        f"Token type: [bold]{token.token_type.value}[/bold]",
        f"Min legs: {token.min_legs}",
        f"Max stake: ${token.max_stake:.2f}",
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


def _plan_panel(plan: HedgePlan, title: str) -> Panel:
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
            leg.event_label,
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
    parlay_table.add_row("", "", "", "Stake", f"${plan.stake:.2f}", "")

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
            f"${step.stake:.2f}",
            step.place_by.strftime("%Y-%m-%d %H:%M %Z"),
        )

    footer_lines = [
        f"Parlay win profit after token: ${plan.effective_win_profit:.2f}",
        f"Locked profit: [green]${plan.locked_profit:.2f}[/green]",
        f"Max cash needed: ${plan.max_cash_needed:.2f}",
        f"ROI: {plan.roi * 100:.2f}%",
        f"Guaranteed: {'Yes' if plan.is_guaranteed else 'No'}",
    ]
    for warning in plan.warnings:
        footer_lines.append(f"[yellow]{warning}[/yellow]")

    content = Group(parlay_table, hedge_table, "\n".join(footer_lines))
    return Panel(content, title=title, border_style="green" if plan.is_guaranteed else "yellow")
