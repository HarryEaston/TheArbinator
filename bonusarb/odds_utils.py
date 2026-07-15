"""Odds conversion and lookup helpers."""

from __future__ import annotations

from bonusarb.display import format_selection
from bonusarb.models import BookmakerKey, Game, Leg, Outcome


POLYMARKET_BOOK = "polymarket"


def _polymarket_hedge_token_id(
    game: Game,
    market_key: str,
    hedge_name: str,
    hedge_point: float | None,
) -> str | None:
    """Return the CLOB token id for a Polymarket hedge outcome, if present.

    The synthetic "polymarket" bookmaker carries ``token_id`` on each outcome
    (populated by ``merge_polymarket_odds``). We match by outcome name and, for
    spreads/totals, by exact line so the auto-hedger can place/watch the right
    market without re-discovering it.
    """
    bookmaker = game.bookmakers.get(POLYMARKET_BOOK)
    if not bookmaker:
        return None
    market = bookmaker.markets.get(market_key)
    if not market:
        return None
    for outcome in market.outcomes:
        if outcome.name != hedge_name:
            continue
        if hedge_point is None:
            return outcome.token_id
        if outcome.point is None or abs(outcome.point - hedge_point) > 0.001:
            continue
        return outcome.token_id
    return None


def american_to_decimal(american: float) -> float:
    if american >= 100:
        return 1.0 + american / 100.0
    if american <= -100:
        return 1.0 + 100.0 / abs(american)
    raise ValueError(f"Invalid American odds: {american}")


def decimal_to_american(decimal_odds: float) -> int:
    if decimal_odds < 1.0:
        raise ValueError(f"Invalid decimal odds: {decimal_odds}")
    if decimal_odds >= 2.0:
        return int(round((decimal_odds - 1.0) * 100))
    return int(round(-100.0 / (decimal_odds - 1.0)))


def implied_probability(decimal_odds: float) -> float:
    return 1.0 / decimal_odds


def market_vig(outcomes: tuple[Outcome, ...]) -> float:
    return sum(implied_probability(o.price) for o in outcomes) - 1.0


def normalize_price(price: float, odds_format: str) -> float:
    if odds_format == "decimal":
        return price
    if odds_format == "american":
        return american_to_decimal(price)
    raise ValueError(f"Unsupported odds format: {odds_format}")


def is_push_prone_line(point: float | None) -> bool:
    if point is None:
        return False
    return point == int(point)


BINARY_TEAM_MARKET_KEYS = ("h2h", "to_advance")


def parse_market_keys(market: str) -> tuple[str, ...]:
    keys = tuple(part.strip() for part in market.split(",") if part.strip())
    allowed = {"h2h", "to_advance", "spreads", "totals"}
    invalid = [key for key in keys if key not in allowed]
    if invalid:
        raise ValueError(f"Unsupported market keys: {', '.join(invalid)}")
    if not keys:
        raise ValueError("At least one market key is required.")
    return keys


def get_h2h_outcomes(game: Game, book: BookmakerKey) -> tuple[Outcome, Outcome] | None:
    bookmaker = game.bookmakers.get(book)
    if not bookmaker:
        return None
    market = bookmaker.markets.get("h2h")
    if not market or len(market.outcomes) != 2:
        return None
    return market.outcomes[0], market.outcomes[1]


def is_two_way_market(game: Game, book: BookmakerKey, market_key: str = "h2h") -> bool:
    bookmaker = game.bookmakers.get(book)
    if not bookmaker:
        return False
    market = bookmaker.markets.get(market_key)
    if not market or len(market.outcomes) != 2:
        return False

    if market_key in {"h2h", "to_advance"}:
        outcome_names = {outcome.name for outcome in market.outcomes}
        return outcome_names == {game.home_team, game.away_team}

    if market_key == "spreads":
        teams = {game.home_team, game.away_team}
        points = []
        for outcome in market.outcomes:
            if outcome.name not in teams or outcome.point is None:
                return False
            if is_push_prone_line(outcome.point):
                return False
            points.append(outcome.point)
        return abs(points[0] + points[1]) < 0.001

    if market_key == "totals":
        names = {outcome.name for outcome in market.outcomes}
        if names != {"Over", "Under"}:
            return False
        points = [outcome.point for outcome in market.outcomes]
        if any(point is None for point in points):
            return False
        if is_push_prone_line(points[0]):
            return False
        return abs(points[0] - points[1]) < 0.001

    return True


def opposite_team(game: Game, team: str) -> str | None:
    teams = {game.home_team, game.away_team}
    if team not in teams:
        return None
    return game.away_team if team == game.home_team else game.home_team


def opposite_outcome(game: Game, selection: str) -> str | None:
    return opposite_team(game, selection)


def is_exact_spread_hedge(token_outcome: Outcome, hedge_outcome: Outcome) -> bool:
    if token_outcome.point is None or hedge_outcome.point is None:
        return False
    if is_push_prone_line(token_outcome.point) or is_push_prone_line(hedge_outcome.point):
        return False
    return abs(token_outcome.point + hedge_outcome.point) < 0.001


def is_exact_total_hedge(token_outcome: Outcome, hedge_outcome: Outcome) -> bool:
    if token_outcome.point is None or hedge_outcome.point is None:
        return False
    if is_push_prone_line(token_outcome.point):
        return False
    token_side = token_outcome.name
    hedge_side = hedge_outcome.name
    if {token_side, hedge_side} != {"Over", "Under"}:
        return False
    return abs(token_outcome.point - hedge_outcome.point) < 0.001


def best_spread_hedge(
    game: Game,
    token_outcome: Outcome,
    hedge_books: tuple[BookmakerKey, ...],
) -> tuple[BookmakerKey, Outcome] | None:
    opposite = opposite_team(game, token_outcome.name)
    if opposite is None:
        return None

    best: tuple[BookmakerKey, Outcome] | None = None
    best_price = 0.0
    for book in hedge_books:
        if not is_two_way_market(game, book, "spreads"):
            continue
        market = game.bookmakers[book].markets["spreads"]
        for outcome in market.outcomes:
            if outcome.name != opposite:
                continue
            if not is_exact_spread_hedge(token_outcome, outcome):
                continue
            if outcome.price > best_price:
                best_price = outcome.price
                best = (book, outcome)
    return best


def best_total_hedge(
    game: Game,
    token_outcome: Outcome,
    hedge_books: tuple[BookmakerKey, ...],
) -> tuple[BookmakerKey, Outcome] | None:
    hedge_name = "Under" if token_outcome.name == "Over" else "Over"
    best: tuple[BookmakerKey, Outcome] | None = None
    best_price = 0.0
    for book in hedge_books:
        if not is_two_way_market(game, book, "totals"):
            continue
        market = game.bookmakers[book].markets["totals"]
        for outcome in market.outcomes:
            if outcome.name != hedge_name:
                continue
            if not is_exact_total_hedge(token_outcome, outcome):
                continue
            if outcome.price > best_price:
                best_price = outcome.price
                best = (book, outcome)
    return best


def best_binary_team_hedge(
    game: Game,
    selection: str,
    hedge_books: tuple[BookmakerKey, ...],
    hedge_market_keys: tuple[str, ...] = BINARY_TEAM_MARKET_KEYS,
) -> tuple[BookmakerKey, float] | None:
    if not any(
        is_two_way_market(game, book, market_key)
        for book in hedge_books
        for market_key in hedge_market_keys
    ):
        return None

    opposite = opposite_team(game, selection)
    if opposite is None:
        return None

    best_book: BookmakerKey | None = None
    best_odds = 0.0
    for book in hedge_books:
        bookmaker = game.bookmakers.get(book)
        if not bookmaker:
            continue
        for market_key in hedge_market_keys:
            market = bookmaker.markets.get(market_key)
            if not market or not is_two_way_market(game, book, market_key):
                continue
            for outcome in market.outcomes:
                if outcome.name == opposite and outcome.price > best_odds:
                    best_odds = outcome.price
                    best_book = book
    if best_book is None:
        return None
    return best_book, best_odds


def best_h2h_hedge(
    game: Game,
    selection: str,
    hedge_books: tuple[BookmakerKey, ...],
) -> tuple[BookmakerKey, float] | None:
    return best_binary_team_hedge(game, selection, hedge_books)


def best_hedge_for_selection(
    game: Game,
    selection: str,
    hedge_books: tuple[BookmakerKey, ...],
    market_key: str = "h2h",
    token_point: float | None = None,
) -> tuple[BookmakerKey, float, float | None] | None:
    if market_key in {"h2h", "to_advance"}:
        result = best_binary_team_hedge(game, selection, hedge_books)
        if result is None:
            return None
        book, odds = result
        return book, odds, None

    if market_key == "spreads" and token_point is not None:
        token_outcome = Outcome(selection, 0.0, token_point)
        hedge = best_spread_hedge(game, token_outcome, hedge_books)
        if hedge is None:
            return None
        book, outcome = hedge
        return book, outcome.price, outcome.point

    if market_key == "totals" and token_point is not None:
        token_outcome = Outcome(selection, 0.0, token_point)
        hedge = best_total_hedge(game, token_outcome, hedge_books)
        if hedge is None:
            return None
        book, outcome = hedge
        return book, outcome.price, outcome.point

    return None


def _make_leg(
    game: Game,
    market_key: str,
    selection: str,
    opposite_selection: str,
    token_book: BookmakerKey,
    token_odds: float,
    hedge_book: BookmakerKey,
    hedge_odds: float,
    token_point: float | None = None,
    hedge_point: float | None = None,
    *,
    polymarket_event_slug: str | None = None,
    hedge_token_id: str | None = None,
) -> Leg:
    return Leg(
        game_id=game.id,
        event_label=f"{game.away_team} @ {game.home_team}",
        commence_time=game.commence_time,
        selection=selection,
        opposite_selection=opposite_selection,
        token_book=token_book,
        token_odds=token_odds,
        hedge_book=hedge_book,
        hedge_odds=hedge_odds,
        market_key=market_key,
        token_point=token_point,
        hedge_point=hedge_point,
        polymarket_event_slug=polymarket_event_slug,
        hedge_token_id=hedge_token_id,
    )


def _polymarket_context(
    game: Game,
    market_key: str,
    hedge_book: BookmakerKey,
    opposite_selection: str,
    hedge_point: float | None,
    slug_by_game_id: dict[str, str],
) -> tuple[str | None, str | None]:
    """Resolve (event_slug, hedge_token_id) when the hedge is on Polymarket."""
    if hedge_book != POLYMARKET_BOOK:
        return None, None
    token_id = _polymarket_hedge_token_id(game, market_key, opposite_selection, hedge_point)
    return slug_by_game_id.get(game.id), token_id


def _token_odds_for_binary_team_selection(
    game: Game,
    token_book: BookmakerKey,
    selection: str,
    token_market_keys: tuple[str, ...],
) -> tuple[str, float] | None:
    bookmaker = game.bookmakers.get(token_book)
    if not bookmaker:
        return None
    for market_key in token_market_keys:
        if not is_two_way_market(game, token_book, market_key):
            continue
        market = bookmaker.markets.get(market_key)
        if not market:
            continue
        for outcome in market.outcomes:
            if outcome.name == selection:
                return market_key, outcome.price
    return None


def build_h2h_leg(
    game: Game,
    selection: str,
    token_book: BookmakerKey,
    hedge_books: tuple[BookmakerKey, ...],
    *,
    slug_by_game_id: dict[str, str] | None = None,
) -> Leg | None:
    token_quote = _token_odds_for_binary_team_selection(
        game,
        token_book,
        selection,
        ("h2h",),
    )
    if token_quote is None:
        return None
    _market_key, token_odds = token_quote

    hedge = best_binary_team_hedge(game, selection, hedge_books)
    if hedge is None:
        return None
    hedge_book, hedge_odds = hedge
    opposite = opposite_team(game, selection)
    if opposite is None:
        return None

    slug, token_id = _polymarket_context(
        game, "h2h", hedge_book, opposite, None, slug_by_game_id or {}
    )
    return _make_leg(
        game,
        "h2h",
        selection,
        opposite,
        token_book,
        token_odds,
        hedge_book,
        hedge_odds,
        polymarket_event_slug=slug,
        hedge_token_id=token_id,
    )


def build_to_advance_leg(
    game: Game,
    selection: str,
    token_book: BookmakerKey,
    hedge_books: tuple[BookmakerKey, ...],
    *,
    slug_by_game_id: dict[str, str] | None = None,
) -> Leg | None:
    token_quote = _token_odds_for_binary_team_selection(
        game,
        token_book,
        selection,
        ("to_advance", "h2h"),
    )
    if token_quote is None:
        return None
    token_market_key, token_odds = token_quote

    hedge = best_binary_team_hedge(game, selection, hedge_books)
    if hedge is None:
        return None
    hedge_book, hedge_odds = hedge
    opposite = opposite_team(game, selection)
    if opposite is None:
        return None

    # The hedge is resolved on the same binary market the token side uses.
    slug, token_id = _polymarket_context(
        game, token_market_key, hedge_book, opposite, None, slug_by_game_id or {}
    )
    return _make_leg(
        game,
        token_market_key,
        selection,
        opposite,
        token_book,
        token_odds,
        hedge_book,
        hedge_odds,
        polymarket_event_slug=slug,
        hedge_token_id=token_id,
    )


def build_spread_leg(
    game: Game,
    token_outcome: Outcome,
    token_book: BookmakerKey,
    hedge_books: tuple[BookmakerKey, ...],
    *,
    slug_by_game_id: dict[str, str] | None = None,
) -> Leg | None:
    if not is_two_way_market(game, token_book, "spreads"):
        return None
    if token_outcome.point is None or is_push_prone_line(token_outcome.point):
        return None

    hedge = best_spread_hedge(game, token_outcome, hedge_books)
    if hedge is None:
        return None
    hedge_book, hedge_outcome = hedge
    opposite = opposite_team(game, token_outcome.name)
    if opposite is None:
        return None

    slug, token_id = _polymarket_context(
        game, "spreads", hedge_book, opposite, hedge_outcome.point, slug_by_game_id or {}
    )
    return _make_leg(
        game,
        "spreads",
        token_outcome.name,
        opposite,
        token_book,
        token_outcome.price,
        hedge_book,
        hedge_outcome.price,
        token_point=token_outcome.point,
        hedge_point=hedge_outcome.point,
        polymarket_event_slug=slug,
        hedge_token_id=token_id,
    )


def build_total_leg(
    game: Game,
    token_outcome: Outcome,
    token_book: BookmakerKey,
    hedge_books: tuple[BookmakerKey, ...],
    *,
    slug_by_game_id: dict[str, str] | None = None,
) -> Leg | None:
    if not is_two_way_market(game, token_book, "totals"):
        return None
    if token_outcome.point is None or is_push_prone_line(token_outcome.point):
        return None
    if token_outcome.name not in {"Over", "Under"}:
        return None

    hedge = best_total_hedge(game, token_outcome, hedge_books)
    if hedge is None:
        return None
    hedge_book, hedge_outcome = hedge
    hedge_name = "Under" if token_outcome.name == "Over" else "Over"

    slug, token_id = _polymarket_context(
        game, "totals", hedge_book, hedge_name, hedge_outcome.point, slug_by_game_id or {}
    )
    return _make_leg(
        game,
        "totals",
        token_outcome.name,
        hedge_name,
        token_book,
        token_outcome.price,
        hedge_book,
        hedge_outcome.price,
        token_point=token_outcome.point,
        hedge_point=hedge_outcome.point,
        polymarket_event_slug=slug,
        hedge_token_id=token_id,
    )


def build_leg(
    game: Game,
    selection: str,
    token_book: BookmakerKey,
    hedge_books: tuple[BookmakerKey, ...],
    market_key: str = "h2h",
    *,
    slug_by_game_id: dict[str, str] | None = None,
) -> Leg | None:
    if market_key == "h2h":
        return build_h2h_leg(game, selection, token_book, hedge_books, slug_by_game_id=slug_by_game_id)
    if market_key == "to_advance":
        return build_to_advance_leg(game, selection, token_book, hedge_books, slug_by_game_id=slug_by_game_id)
    return None


def enumerate_game_legs(
    game: Game,
    token_book: BookmakerKey,
    hedge_books: tuple[BookmakerKey, ...],
    market_keys: tuple[str, ...],
    *,
    slug_by_game_id: dict[str, str] | None = None,
) -> list[Leg]:
    slug_by_game_id = slug_by_game_id or {}
    legs: list[Leg] = []
    for market_key in market_keys:
        if market_key == "h2h":
            for team in (game.home_team, game.away_team):
                leg = build_h2h_leg(game, team, token_book, hedge_books, slug_by_game_id=slug_by_game_id)
                if leg is not None:
                    legs.append(leg)
        elif market_key == "to_advance":
            for team in (game.home_team, game.away_team):
                leg = build_to_advance_leg(game, team, token_book, hedge_books, slug_by_game_id=slug_by_game_id)
                if leg is not None:
                    legs.append(leg)
        elif market_key == "spreads":
            bookmaker = game.bookmakers.get(token_book)
            if not bookmaker:
                continue
            market = bookmaker.markets.get("spreads")
            if not market:
                continue
            for outcome in market.outcomes:
                leg = build_spread_leg(game, outcome, token_book, hedge_books, slug_by_game_id=slug_by_game_id)
                if leg is not None:
                    legs.append(leg)
        elif market_key == "totals":
            bookmaker = game.bookmakers.get(token_book)
            if not bookmaker:
                continue
            market = bookmaker.markets.get("totals")
            if not market:
                continue
            for outcome in market.outcomes:
                leg = build_total_leg(game, outcome, token_book, hedge_books, slug_by_game_id=slug_by_game_id)
                if leg is not None:
                    legs.append(leg)
    return legs


def leg_selection_display(leg: Leg) -> str:
    return format_selection(leg.market_key, leg.selection, leg.token_point)


def leg_hedge_display(leg: Leg) -> str:
    return format_selection(leg.market_key, leg.opposite_selection, leg.hedge_point)


def combined_decimal_odds(legs: tuple[Leg, ...]) -> float:
    odds = 1.0
    for leg in legs:
        odds *= leg.token_odds
    return odds
