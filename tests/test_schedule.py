"""Tests for schedule feasibility."""

from datetime import datetime

from bonusarb.models import Leg
from bonusarb.schedule import is_schedule_feasible


def _leg(game_id: str, commence: str) -> Leg:
    return Leg(
        game_id=game_id,
        event_label=game_id,
        commence_time=datetime.fromisoformat(commence.replace("Z", "+00:00")),
        selection="Team A",
        opposite_selection="Team B",
        token_book="fanduel",
        token_odds=2.0,
        hedge_book="draftkings",
        hedge_odds=2.0,
    )


def test_schedule_feasible_for_spaced_games():
    legs = (
        _leg("g1", "2026-07-03T00:00:00Z"),
        _leg("g2", "2026-07-03T05:00:00Z"),
    )
    ok, issues = is_schedule_feasible(legs, "basketball_nba", min_gap_minutes=30)
    assert ok
    assert issues == []


def test_schedule_rejects_overlapping_games():
    legs = (
        _leg("g1", "2026-07-03T00:00:00Z"),
        _leg("g2", "2026-07-03T01:00:00Z"),
    )
    ok, issues = is_schedule_feasible(legs, "basketball_nba", min_gap_minutes=30)
    assert not ok
    assert issues
