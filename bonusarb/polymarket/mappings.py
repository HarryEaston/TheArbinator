"""Sportsbook-to-Polymarket event mapping types.

Auto-discovery (see ``discovery.py``) matches each sportsbook game to a
Polymarket event by team names and start time, producing an ``EventMapping``.
``find_mapping`` is used by the merge step to attach Polymarket hedge odds to a
sportsbook ``Game``. Auto-discovered mappings are unverified, so the merge step
surfaces a "verify settlement" warning at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bonusarb.models import Game


@dataclass(frozen=True)
class OutcomeMapping:
    polymarket_outcome: str
    team: str


@dataclass(frozen=True)
class EventMapping:
    sport_key: str
    home_team: str
    away_team: str
    polymarket_event_slug: str
    settlement: str
    outcomes: tuple[OutcomeMapping, ...]
    commence_time: datetime
    commence_window_minutes: int = 240
    verified: bool = False

    @property
    def polymarket_slug(self) -> str:
        """Backward-compatible alias for the main Polymarket event slug."""
        return self.polymarket_event_slug

    @property
    def team_by_outcome(self) -> dict[str, str]:
        return {outcome.polymarket_outcome: outcome.team for outcome in self.outcomes}

    @property
    def outcome_by_team(self) -> dict[str, str]:
        return {outcome.team: outcome.polymarket_outcome for outcome in self.outcomes}

    def matches_game(self, game: Game) -> bool:
        if game.sport_key != self.sport_key:
            return False
        if {game.home_team, game.away_team} != {self.home_team, self.away_team}:
            return False
        window = timedelta(minutes=self.commence_window_minutes)
        if abs(game.commence_time - self.commence_time) > window:
            return False
        return True


def find_mapping(
    game: Game,
    mappings: list[EventMapping],
) -> EventMapping | None:
    for mapping in mappings:
        if mapping.matches_game(game):
            return mapping
    return None
