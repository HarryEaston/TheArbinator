"""Formatting helpers for terminal display."""

from __future__ import annotations


def format_selection(market_key: str, selection: str, point: float | None) -> str:
    if market_key in {"h2h", "to_advance"} or point is None:
        return selection
    if market_key == "totals":
        return f"{selection} {point:g}"
    sign = "+" if point > 0 else ""
    return f"{selection} {sign}{point:g}"


def market_label(market_key: str) -> str:
    labels = {
        "h2h": "Moneyline",
        "to_advance": "Team to Advance",
        "spreads": "Spread",
        "totals": "Total",
    }
    return labels.get(market_key, market_key)
