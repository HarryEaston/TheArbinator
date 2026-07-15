"""Merge Polymarket hedge odds into sportsbook Game objects.

For each sportsbook game that has a mapping, fetch all hedge-relevant Polymarket
markets for the mapped event (moneyline, spreads, and totals) and attach a
synthetic ``BookmakerOdds(key="polymarket")``. The existing optimizer then
treats Polymarket like any other hedge venue.

Mappings are auto-discovered (unverified). Unverified mappings raise a summary
"verify settlement" warning rather than being skipped, so the tool surfaces
opportunities instead of hiding them.
"""

from __future__ import annotations

from typing import Sequence

from bonusarb.models import BookmakerOdds, Game, Market, Outcome
from bonusarb.polymarket.client import PolymarketClient, PolymarketError
from bonusarb.polymarket.mappings import EventMapping, find_mapping


POLYMARKET_BOOK_KEY = "polymarket"


def merge_polymarket_odds(
    games: Sequence[Game],
    mappings: list[EventMapping],
    client: PolymarketClient,
    *,
    allow_fetch: bool = True,
    force_fetch: bool = False,
) -> tuple[list[Game], list[str]]:
    """Return new Game objects with Polymarket hedge odds attached, plus warnings."""
    warnings: list[str] = []
    merged_games: list[Game] = []
    unverified_used = 0

    for game in games:
        mapping = find_mapping(game, mappings)
        if mapping is None:
            merged_games.append(game)
            continue

        if not mapping.verified:
            unverified_used += 1

        # Resolve the sportsbook team names to Polymarket outcome names via the
        # mapping, so get_game_markets' internal `set(outcomes) != teams` check
        # passes even when the two feeds spell a team differently (e.g.
        # "Portland Fire" vs "PortlandFire").
        poly_home = mapping.outcome_by_team.get(game.home_team, game.home_team)
        poly_away = mapping.outcome_by_team.get(game.away_team, game.away_team)
        relabel = mapping.team_by_outcome

        try:
            game_markets = client.get_game_markets(
                mapping.polymarket_event_slug,
                poly_home,
                poly_away,
                allow_fetch=allow_fetch,
                force_fetch=force_fetch,
            )
        except PolymarketError as exc:
            warnings.append(
                f"Polymarket fetch failed for {mapping.polymarket_event_slug}: {exc}"
            )
            merged_games.append(game)
            continue

        if game_markets is None or not game_markets.markets:
            warnings.append(
                f"Polymarket markets for {mapping.polymarket_event_slug} "
                f"({game.away_team} @ {game.home_team}) were not available; skipped."
            )
            merged_games.append(game)
            continue

        polymarket_markets: dict[str, Market] = {}
        for market_key, outcomes in game_markets.markets.items():
            polymarket_markets[market_key] = Market(
                key=market_key,
                outcomes=tuple(
                    Outcome(
                        name=relabel.get(outcome.name, outcome.name),
                        price=outcome.decimal_odds,
                        point=outcome.point,
                        token_id=outcome.token_id,
                    )
                    for outcome in outcomes
                ),
                last_update=game_markets.last_update,
            )

        polymarket_book = BookmakerOdds(
            key=POLYMARKET_BOOK_KEY,
            title="Polymarket",
            markets=polymarket_markets,
        )

        new_bookmakers = dict(game.bookmakers)
        new_bookmakers[POLYMARKET_BOOK_KEY] = polymarket_book
        merged_games.append(
            Game(
                id=game.id,
                sport_key=game.sport_key,
                sport_title=game.sport_title,
                commence_time=game.commence_time,
                home_team=game.home_team,
                away_team=game.away_team,
                bookmakers=new_bookmakers,
            )
        )

    if any(POLYMARKET_BOOK_KEY in g.bookmakers for g in merged_games):
        warnings.append(
            "Polymarket hedge odds are post–sports-taker-fee (top-of-book) and may "
            "have limited liquidity; verify fill depth and slippage before placing hedges."
        )
    if unverified_used:
        warnings.append(
            f"{unverified_used} Polymarket mapping(s) are unverified (e.g. auto-discovered). "
            "Confirm each market settles exactly like your sportsbook bet before betting."
        )

    return merged_games, warnings
