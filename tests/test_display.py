"""Tests for terminal display formatting."""

from datetime import datetime, timezone

from bonusarb.display import format_display_time, format_event_cell


def test_format_display_time_converts_utc_to_eastern():
    # 2026-07-08 03:16 UTC -> 2026-07-07 11:16 PM EDT
    dt = datetime(2026, 7, 8, 3, 16, tzinfo=timezone.utc)
    text = format_display_time(dt)
    assert "2026-07-07" in text
    assert "11:16 PM" in text
    assert "EDT" in text


def test_format_event_cell_includes_eastern_start():
    dt = datetime(2026, 7, 8, 3, 16, tzinfo=timezone.utc)
    text = format_event_cell("A @ B", dt, plain=True)
    assert "A @ B" in text
    assert "EDT" in text
