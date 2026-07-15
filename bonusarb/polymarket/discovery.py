"""Auto-discovery of Polymarket events for a sport.

Instead of requiring a hand-written mapping per game, this module lists a
sport's live Polymarket events (via its Gamma tag) and matches them to the
sportsbook games from The Odds API by team names and game start time.

Matching is intentionally strict: a game only auto-maps when both team names
match exactly and the Polymarket start time is within a window of the
sportsbook commence time. This avoids pairing the wrong game (teams meet many
times per season) or mismatched settlement rules. Auto-discovered mappings are
never marked ``verified``; callers surface a "verify settlement" warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Sequence

from bonusarb.models import Game
from bonusarb.polymarket.client import (
    PolymarketClient,
    PolymarketError,
    _parse_stringified_array,
)
from bonusarb.polymarket.mappings import EventMapping, OutcomeMapping

# The Odds API sport_key -> Polymarket Gamma sport tag id, for the supported
# leagues. Tag ids come from the Gamma ``/sports`` and ``/tags`` endpoints.
SPORT_TAG_IDS: dict[str, int] = {
    "baseball_mlb": 100381,
    "basketball_nba": 745,
    "basketball_wnba": 100254,
    "icehockey_nhl": 899,
    "americanfootball_nfl": 450,
    "mma_mixed_martial_arts": 279,
}

DEFAULT_MATCH_WINDOW_MINUTES = 720


@dataclass(frozen=True)
class DiscoveredEvent:
    slug: str
    home_team: str
    away_team: str
    start_time: datetime | None


def _normalize(name: str) -> str:
    return "".join(ch.lower() for ch in name if ch.isalnum())


def _pair_team_names(
    game_teams: tuple[str, str],
    polymarket_teams: tuple[str, str],
) -> dict[str, str] | None:
    """Map each sportsbook team name to its Polymarket outcome name.

    Matches by normalized name (case/punctuation/diacritics-agnostic) so a
    team like "Portland Fire" (Odds API) still pairs with "PortlandFire"
    (Polymarket). Returns ``{game_team: polymarket_team}`` or ``None`` if
    either team cannot be paired unambiguously.
    """
    pairs: dict[str, str] = {}
    used: set[str] = set()
    for game_team in game_teams:
        game_norm = _normalize(game_team)
        for poly_team in polymarket_teams:
            if poly_team in used:
                continue
            if game_norm == _normalize(poly_team):
                pairs[game_team] = poly_team
                used.add(poly_team)
                break
    if len(pairs) != 2:
        return None
    return pairs


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    # Gamma sometimes returns "2026-07-03 23:10:00+00"; make it ISO-friendly.
    text = text.replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _teams_from_title(title: str | None) -> list[str] | None:
    if not title:
        return None
    # Sibling events append a market descriptor, e.g. "A vs. B - Halftime Result".
    base = title.split(" - ", 1)[0]
    for separator in (" vs. ", " vs ", " @ "):
        if separator in base:
            left, right = base.split(separator, 1)
            left, right = left.strip(), right.strip()
            if left and right:
                return [left, right]
    return None


def _event_team_names(event: dict) -> list[str] | None:
    # A 2-way moneyline (MLB/NBA/NHL/NFL, two fighters for UFC, or two
    # players for tennis) lists the names directly.
    for market in event.get("markets") or []:
        if market.get("sportsMarketType") == "moneyline":
            outcomes = [str(o) for o in _parse_stringified_array(market.get("outcomes"))]
            if len(outcomes) == 2 and set(outcomes) != {"Yes", "No"} and "Draw" not in outcomes:
                return outcomes
    return _teams_from_title(event.get("title"))


def _event_start(event: dict) -> datetime | None:
    return _parse_iso(event.get("startTime") or event.get("gameStartTime"))


def _discovered_events(
    sport_key: str,
    client: PolymarketClient,
    *,
    allow_fetch: bool,
    force_fetch: bool,
) -> tuple[list[DiscoveredEvent], list[str]]:
    tag_id = SPORT_TAG_IDS.get(sport_key)
    if tag_id is None:
        return [], []

    try:
        raw_events = client.list_events_for_tag(
            tag_id,
            allow_fetch=allow_fetch,
            force_fetch=force_fetch,
        )
    except PolymarketError as exc:
        return [], [f"Polymarket discovery failed for {sport_key}: {exc}"]

    # US team sports list the away team first (Gamma "ordering": "away").
    # A single game spans several sibling events (halftime, more-markets, etc.).
    # Collapse them to one entry per (teams, date), preferring the main event
    # (title without a " - <market>" suffix) for a clean slug and label.
    collapsed: dict[tuple, tuple[DiscoveredEvent, bool]] = {}
    for raw in raw_events:
        slug = raw.get("slug")
        names = _event_team_names(raw)
        if not slug or not names or len(names) != 2:
            continue
        # Skip non-game events (season futures, player-prop aggregates); only
        # true single-game events carry a gameId, and only they can resolve to
        # hedgeable game-line markets.
        if not raw.get("gameId"):
            continue
        away_team, home_team = names[0], names[1]

        start = _event_start(raw)
        event = DiscoveredEvent(
            slug=slug,
            home_team=home_team,
            away_team=away_team,
            start_time=start,
        )
        is_main = " - " not in (raw.get("title") or "")
        key = (
            frozenset({_normalize(home_team), _normalize(away_team)}),
            start.date() if start else None,
        )
        existing = collapsed.get(key)
        if existing is None or (is_main and not existing[1]):
            collapsed[key] = (event, is_main)

    return [event for event, _ in collapsed.values()], []


def list_discovered_events(
    sport_key: str,
    client: PolymarketClient,
    *,
    allow_fetch: bool = True,
    force_fetch: bool = False,
) -> tuple[list[DiscoveredEvent], list[str]]:
    """Return the sport's live single-game Polymarket events (for manual mode)."""
    return _discovered_events(
        sport_key,
        client,
        allow_fetch=allow_fetch,
        force_fetch=force_fetch,
    )


def discovered_event_to_mapping(sport_key: str, event: DiscoveredEvent) -> EventMapping:
    return EventMapping(
        sport_key=sport_key,
        home_team=event.home_team,
        away_team=event.away_team,
        polymarket_event_slug=event.slug,
        settlement="Auto-discovered from Polymarket; verify settlement rules match your bet.",
        outcomes=(
            OutcomeMapping(event.home_team, event.home_team),
            OutcomeMapping(event.away_team, event.away_team),
        ),
        commence_time=event.start_time or datetime.now(timezone.utc),
        verified=False,
    )


def discover_event_mappings(
    sport_key: str,
    games: Sequence[Game],
    client: PolymarketClient,
    *,
    allow_fetch: bool = True,
    force_fetch: bool = False,
    window_minutes: int = DEFAULT_MATCH_WINDOW_MINUTES,
) -> tuple[list[EventMapping], list[str]]:
    """Match sportsbook games to Polymarket events by team names and start time."""
    events, warnings = _discovered_events(
        sport_key,
        client,
        allow_fetch=allow_fetch,
        force_fetch=force_fetch,
    )
    if not events:
        return [], warnings

    index: dict[frozenset[str], list[DiscoveredEvent]] = {}
    for event in events:
        key = frozenset({_normalize(event.home_team), _normalize(event.away_team)})
        index.setdefault(key, []).append(event)

    window = timedelta(minutes=window_minutes)
    mappings: list[EventMapping] = []
    for game in games:
        key = frozenset({_normalize(game.home_team), _normalize(game.away_team)})
        candidates = index.get(key)
        if not candidates:
            continue

        match = _closest_event(candidates, game.commence_time, window)
        if match is None:
            continue

        # Pair sportsbook team names to Polymarket outcome names by normalized
        # name, so formatting differences (e.g. "Portland Fire" vs
        # "PortlandFire") don't drop an otherwise-valid match. The mapping's
        # home/away stay as sportsbook names so find_mapping/matches_game still
        # line up with the Game; outcomes carry the Polymarket names for the
        # merge step to relabel hedge odds back to sportsbook names.
        pairs = _pair_team_names(
            (game.home_team, game.away_team),
            (match.home_team, match.away_team),
        )
        if pairs is None:
            continue

        mappings.append(
            EventMapping(
                sport_key=sport_key,
                home_team=game.home_team,
                away_team=game.away_team,
                polymarket_event_slug=match.slug,
                settlement="Auto-discovered from Polymarket; verify settlement rules match your bet.",
                outcomes=(
                    OutcomeMapping(polymarket_outcome=pairs[game.home_team], team=game.home_team),
                    OutcomeMapping(polymarket_outcome=pairs[game.away_team], team=game.away_team),
                ),
                commence_time=game.commence_time,
                verified=False,
            )
        )

    if mappings:
        warnings.append(
            f"Auto-matched {len(mappings)} Polymarket event(s) for {sport_key} by team "
            "and start time. Confirm each settles like your sportsbook bet before betting."
        )
    return mappings, warnings


def _closest_event(
    candidates: list[DiscoveredEvent],
    commence_time: datetime,
    window: timedelta,
) -> DiscoveredEvent | None:
    best: DiscoveredEvent | None = None
    best_delta: timedelta | None = None
    for event in candidates:
        if event.start_time is None:
            continue
        delta = abs(event.start_time - commence_time)
        if delta > window:
            continue
        if best_delta is None or delta < best_delta:
            best = event
            best_delta = delta
    return best
