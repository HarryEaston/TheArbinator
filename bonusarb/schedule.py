"""Schedule feasibility for sequential hedging."""

from __future__ import annotations

from datetime import datetime, timedelta

from bonusarb.models import Leg
from bonusarb.config import DEFAULT_GAME_DURATION_MINUTES, DEFAULT_MIN_GAP_MINUTES, SPORT_DURATION_MINUTES


def sport_duration_minutes(sport_key: str) -> int:
    for prefix, minutes in SPORT_DURATION_MINUTES.items():
        if sport_key.startswith(prefix):
            return minutes
    return DEFAULT_GAME_DURATION_MINUTES


def expected_finish_time(leg: Leg, sport_key: str) -> datetime:
    return leg.commence_time + timedelta(minutes=sport_duration_minutes(sport_key))


def is_schedule_feasible(
    legs: tuple[Leg, ...],
    sport_key: str,
    min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES,
) -> tuple[bool, list[str]]:
    if len(legs) <= 1:
        return True, []

    ordered = sorted(legs, key=lambda leg: leg.commence_time)
    issues: list[str] = []
    gap = timedelta(minutes=min_gap_minutes)

    for index in range(1, len(ordered)):
        previous = ordered[index - 1]
        current = ordered[index]
        previous_finish = expected_finish_time(previous, sport_key)
        if current.commence_time < previous_finish + gap:
            issues.append(
                f"{current.event_label} starts before {previous.event_label} should be final "
                f"(need {min_gap_minutes} min buffer)."
            )
    return len(issues) == 0, issues


def order_legs_for_hedging(legs: tuple[Leg, ...]) -> tuple[Leg, ...]:
    return tuple(sorted(legs, key=lambda leg: leg.commence_time))
