"""Formatting helpers for terminal display."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# User-facing times: Eastern (America/Toronto — same as US Eastern, handles EST/EDT).
DISPLAY_TZ = ZoneInfo("America/Toronto")


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


def to_display_tz(dt: datetime) -> datetime:
    """Convert an aware (or naive UTC) datetime to Eastern for display."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(DISPLAY_TZ)


def format_display_time(dt: datetime) -> str:
    """Format a datetime in Eastern time (EST/EDT depending on date)."""
    return to_display_tz(dt).strftime("%Y-%m-%d %I:%M %p %Z")


def format_event_cell(event_label: str, commence_time: datetime, *, plain: bool = False) -> str:
    """Event name plus game start time (helps distinguish doubleheaders / series)."""
    start = format_display_time(commence_time)
    if plain:
        return f"{event_label}\n{start}"
    return f"{event_label}\n[dim]{start}[/dim]"
