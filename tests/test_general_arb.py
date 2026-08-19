"""Tests for all-pairs 1-leg general arb enumeration."""

from datetime import datetime, timezone

from bonusarb.arb.general import find_two_way_arbs
from bonusarb.models import BookmakerOdds, Game, Market, Outcome


def _game_with_arb() -> Game:
    """FanDuel home 2.20 vs DraftKings away 2.10 — clear moneyline arb."""
    return Game(
        id="game-arb",
        sport_key="basketball_nba",
        sport_title="NBA",
        commence_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        home_team="Boston Celtics",
        away_team="Los Angeles Lakers",
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(
                            Outcome("Boston Celtics", 2.20),
                            Outcome("Los Angeles Lakers", 1.70),
                        ),
                    ),
                    "spreads": Market(
                        key="spreads",
                        outcomes=(
                            Outcome("Boston Celtics", 1.91, -2.5),
                            Outcome("Los Angeles Lakers", 1.91, 2.5),
                        ),
                    ),
                },
            ),
            "draftkings": BookmakerOdds(
                key="draftkings",
                title="DraftKings",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(
                            Outcome("Boston Celtics", 1.75),
                            Outcome("Los Angeles Lakers", 2.10),
                        ),
                    ),
                    "spreads": Market(
                        key="spreads",
                        outcomes=(
                            Outcome("Boston Celtics", 1.91, -2.5),
                            Outcome("Los Angeles Lakers", 1.91, 2.5),
                        ),
                    ),
                },
            ),
            "polymarket": BookmakerOdds(
                key="polymarket",
                title="Polymarket",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(
                            Outcome("Boston Celtics", 1.80, token_id="t-bos"),
                            Outcome("Los Angeles Lakers", 2.15, token_id="t-lal"),
                        ),
                    ),
                },
            ),
        },
    )


def _game_no_arb() -> Game:
    return Game(
        id="game-vig",
        sport_key="basketball_nba",
        sport_title="NBA",
        commence_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        home_team="Team A",
        away_team="Team B",
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(
                            Outcome("Team A", 1.91),
                            Outcome("Team B", 1.91),
                        ),
                    ),
                },
            ),
            "draftkings": BookmakerOdds(
                key="draftkings",
                title="DraftKings",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(
                            Outcome("Team A", 1.90),
                            Outcome("Team B", 1.92),
                        ),
                    ),
                },
            ),
        },
    )


def test_finds_fanduel_vs_draftkings_h2h_arb():
    arbs = find_two_way_arbs([_game_with_arb()], capital=100.0, market_key="h2h")
    assert arbs
    top = arbs[0]
    assert top.roi == max(a.roi for a in arbs)
    books = {top.book_a, top.book_b}
    assert books <= {"fanduel", "draftkings", "polymarket"}
    assert top.locked_profit > 0
    assert top.edge > 0


def test_finds_sportsbook_vs_polymarket_arb():
    arbs = find_two_way_arbs([_game_with_arb()], capital=100.0, market_key="h2h")
    poly_arbs = [a for a in arbs if "polymarket" in {a.book_a, a.book_b}]
    assert poly_arbs
    assert any(a.token_id_a == "t-lal" or a.token_id_b == "t-lal" for a in poly_arbs)


def test_no_arb_when_vig_dominates():
    arbs = find_two_way_arbs([_game_no_arb()], capital=100.0, market_key="h2h")
    assert arbs == []


def test_ranked_by_roi_descending():
    arbs = find_two_way_arbs([_game_with_arb()], capital=100.0, market_key="h2h")
    rois = [a.roi for a in arbs]
    assert rois == sorted(rois, reverse=True)


def test_spreads_require_exact_opposite_line():
    game = _game_with_arb()
    # Add a mismatched DK spread line that should not pair with FD -2.5.
    game.bookmakers["draftkings"].markets["spreads"] = Market(
        key="spreads",
        outcomes=(
            Outcome("Boston Celtics", 2.50, -3.5),
            Outcome("Los Angeles Lakers", 1.60, 3.5),
        ),
    )
    arbs = find_two_way_arbs([game], capital=100.0, market_key="spreads")
    # No exact opposite of FD -2.5 / +2.5 on DK anymore.
    assert all(
        not (
            {a.book_a, a.book_b} == {"fanduel", "draftkings"}
            and a.point_a is not None
            and abs(abs(a.point_a) - 2.5) < 0.001
        )
        for a in arbs
    )


def test_push_prone_spread_excluded():
    game = Game(
        id="game-push",
        sport_key="basketball_nba",
        sport_title="NBA",
        commence_time=datetime(2026, 7, 20, 0, 0, tzinfo=timezone.utc),
        home_team="Boston Celtics",
        away_team="Los Angeles Lakers",
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets={
                    "spreads": Market(
                        key="spreads",
                        outcomes=(
                            Outcome("Boston Celtics", 2.20, -3.0),
                            Outcome("Los Angeles Lakers", 1.70, 3.0),
                        ),
                    ),
                },
            ),
            "draftkings": BookmakerOdds(
                key="draftkings",
                title="DraftKings",
                markets={
                    "spreads": Market(
                        key="spreads",
                        outcomes=(
                            Outcome("Boston Celtics", 1.70, -3.0),
                            Outcome("Los Angeles Lakers", 2.20, 3.0),
                        ),
                    ),
                },
            ),
        },
    )
    assert find_two_way_arbs([game], capital=100.0, market_key="spreads") == []


def test_top_n_limit():
    arbs = find_two_way_arbs(
        [_game_with_arb()], capital=100.0, market_key="h2h", top_n=1
    )
    assert len(arbs) == 1
