"""All-pairs 1-leg arbitrage enumeration across sportsbooks and Polymarket."""

from __future__ import annotations

from bonusarb.arb.two_way import arb_edge, size_two_way_arb
from bonusarb.models import BookmakerKey, Game, Outcome, TwoWayArb
from bonusarb.odds_utils import (
    is_exact_spread_hedge,
    is_exact_total_hedge,
    is_push_prone_line,
    opposite_team,
    parse_market_keys,
)

POLYMARKET_BOOK = "polymarket"

# Venues considered for general arb (sportsbooks + Polymarket).
ARB_VENUES: tuple[BookmakerKey, ...] = (
    "fanduel",
    "draftkings",
    "betmgm",
    "espnbet",
    "polymarket",
)


def _event_label(game: Game) -> str:
    return f"{game.away_team} @ {game.home_team}"


def _opportunity_key(
    game_id: str,
    market_key: str,
    book_a: str,
    selection_a: str,
    point_a: float | None,
    book_b: str,
    selection_b: str,
    point_b: float | None,
) -> frozenset:
    """Direction-invariant identity for a two-sided opportunity."""
    side_a = (book_a, selection_a, point_a)
    side_b = (book_b, selection_b, point_b)
    return frozenset(
        {
            (game_id, market_key, side_a),
            (game_id, market_key, side_b),
        }
    )


def _outcomes_for(game: Game, book: BookmakerKey, market_key: str) -> tuple[Outcome, ...]:
    bookmaker = game.bookmakers.get(book)
    if not bookmaker:
        return ()
    market = bookmaker.markets.get(market_key)
    if not market:
        return ()
    return market.outcomes


def _is_opposite_pair(
    game: Game,
    market_key: str,
    outcome_a: Outcome,
    outcome_b: Outcome,
) -> bool:
    if market_key in {"h2h", "to_advance"}:
        if outcome_a.point is not None or outcome_b.point is not None:
            return False
        opposite = opposite_team(game, outcome_a.name)
        return opposite is not None and outcome_b.name == opposite

    if market_key == "spreads":
        if is_push_prone_line(outcome_a.point) or is_push_prone_line(outcome_b.point):
            return False
        opposite = opposite_team(game, outcome_a.name)
        if opposite is None or outcome_b.name != opposite:
            return False
        return is_exact_spread_hedge(outcome_a, outcome_b)

    if market_key == "totals":
        if is_push_prone_line(outcome_a.point):
            return False
        return is_exact_total_hedge(outcome_a, outcome_b)

    return False


def _polymarket_slug(game: Game, slug_by_game_id: dict[str, str] | None) -> str | None:
    if not slug_by_game_id:
        return None
    return slug_by_game_id.get(game.id)


def find_two_way_arbs(
    games: list[Game] | tuple[Game, ...],
    *,
    capital: float,
    market_key: str = "h2h,spreads,totals",
    venues: tuple[BookmakerKey, ...] = ARB_VENUES,
    top_n: int | None = None,
    slug_by_game_id: dict[str, str] | None = None,
) -> list[TwoWayArb]:
    """Enumerate all-pairs 1-leg arbs and return them ranked by ROI descending."""
    market_keys = parse_market_keys(market_key)
    seen: set[frozenset] = set()
    found: list[TwoWayArb] = []

    for game in games:
        for mk in market_keys:
            for i, book_a in enumerate(venues):
                outcomes_a = _outcomes_for(game, book_a, mk)
                if not outcomes_a:
                    continue
                for book_b in venues[i + 1 :]:
                    outcomes_b = _outcomes_for(game, book_b, mk)
                    if not outcomes_b:
                        continue
                    for outcome_a in outcomes_a:
                        for outcome_b in outcomes_b:
                            if not _is_opposite_pair(game, mk, outcome_a, outcome_b):
                                continue
                            edge = arb_edge(outcome_a.price, outcome_b.price)
                            if edge <= 0:
                                continue
                            key = _opportunity_key(
                                game.id,
                                mk,
                                book_a,
                                outcome_a.name,
                                outcome_a.point,
                                book_b,
                                outcome_b.name,
                                outcome_b.point,
                            )
                            if key in seen:
                                continue
                            sized = size_two_way_arb(
                                outcome_a.price,
                                outcome_b.price,
                                capital,
                                book_a=book_a,
                                book_b=book_b,
                            )
                            if sized is None or sized.locked_profit <= 0:
                                continue
                            seen.add(key)
                            slug = None
                            if book_a == POLYMARKET_BOOK or book_b == POLYMARKET_BOOK:
                                slug = _polymarket_slug(game, slug_by_game_id)
                            found.append(
                                TwoWayArb(
                                    sport_key=game.sport_key,
                                    game_id=game.id,
                                    event_label=_event_label(game),
                                    commence_time=game.commence_time,
                                    market_key=mk,
                                    book_a=book_a,
                                    selection_a=outcome_a.name,
                                    odds_a=outcome_a.price,
                                    point_a=outcome_a.point,
                                    stake_a=sized.stake_a,
                                    book_b=book_b,
                                    selection_b=outcome_b.name,
                                    odds_b=outcome_b.price,
                                    point_b=outcome_b.point,
                                    stake_b=sized.stake_b,
                                    capital=sized.capital_used,
                                    locked_profit=sized.locked_profit,
                                    roi=sized.roi,
                                    edge=edge,
                                    polymarket_event_slug=slug,
                                    token_id_a=outcome_a.token_id
                                    if book_a == POLYMARKET_BOOK
                                    else None,
                                    token_id_b=outcome_b.token_id
                                    if book_b == POLYMARKET_BOOK
                                    else None,
                                )
                            )

    found.sort(key=lambda arb: arb.roi, reverse=True)
    if top_n is not None:
        return found[:top_n]
    return found
