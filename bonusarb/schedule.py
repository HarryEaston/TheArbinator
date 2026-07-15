"""Schedule feasibility for sequential hedging."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bonusarb.models import Game, Leg
from bonusarb.config import DEFAULT_GAME_DURATION_MINUTES, DEFAULT_MIN_GAP_MINUTES, SPORT_DURATION_MINUTES


def matchup_key(game: Game) -> tuple[frozenset[str], datetime]:
    """Identity for a real-world game (teams + start time).

    Doubleheaders share teams but have different ``commence_time`` values.
    """
    return (frozenset({game.home_team, game.away_team}), game.commence_time)


def combo_has_unique_matchups(
    legs: tuple[Leg, ...],
    game_by_id: dict[str, Game],
) -> bool:
    """Return True when no two legs belong to the same matchup."""
    seen: set[tuple[frozenset[str], datetime]] = set()
    for leg in legs:
        game = game_by_id.get(leg.game_id)
        if game is None:
            return False
        key = matchup_key(game)
        if key in seen:
            return False
        seen.add(key)
    return True


def sport_duration_minutes(sport_key: str) -> int:
    for prefix, minutes in SPORT_DURATION_MINUTES.items():
        if sport_key.startswith(prefix):
            return minutes
    return DEFAULT_GAME_DURATION_MINUTES


def expected_finish_time(leg: Leg, sport_key: str) -> datetime:
    return leg.commence_time + timedelta(minutes=sport_duration_minutes(sport_key))


def filter_upcoming_games(
    games: list[Game],
    now: datetime | None = None,
) -> tuple[list[Game], int]:
    """Drop games whose commence_time has already passed.

    Prevents pairing stale pre-game sportsbook odds with live Polymarket
    prices for games already in progress. Returns (upcoming_games, filtered_count).
    """
    cutoff = now or datetime.now(timezone.utc)
    upcoming = [g for g in games if g.commence_time > cutoff]
    return upcoming, len(games) - len(upcoming)


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
