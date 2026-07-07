"""Tests for spread and total market utilities."""

from datetime import datetime

from bonusarb.arb.optimizer import enumerate_candidate_legs, find_best_plans
from bonusarb.models import BookmakerOdds, Game, Market, Outcome, TokenConstraint, TokenType
from bonusarb.display import format_selection
from bonusarb.odds_utils import (
    build_spread_leg,
    build_total_leg,
    is_exact_spread_hedge,
    is_exact_total_hedge,
    is_push_prone_line,
    parse_market_keys,
)
from bonusarb.oddsapi.client import OddsApiClient


def _nba_game() -> Game:
    return Game(
        id="game-1",
        sport_key="basketball_nba",
        sport_title="NBA",
        commence_time=datetime.fromisoformat("2026-07-03T00:00:00+00:00"),
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
                            Outcome("Boston Celtics", 1.91, -2.5),
                            Outcome("Los Angeles Lakers", 1.91, 2.5),
                        ),
                    ),
                    "totals": Market(
                        key="totals",
                        outcomes=(
                            Outcome("Over", 1.91, 221.5),
                            Outcome("Under", 1.91, 221.5),
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
                            Outcome("Boston Celtics", 1.89, -2.5),
                            Outcome("Los Angeles Lakers", 1.93, 2.5),
                        ),
                    ),
                    "totals": Market(
                        key="totals",
                        outcomes=(
                            Outcome("Over", 1.90, 221.5),
                            Outcome("Under", 1.92, 221.5),
                        ),
                    ),
                },
            ),
        },
    )


def test_parse_market_keys():
    assert parse_market_keys("h2h,spreads,totals") == ("h2h", "spreads", "totals")


def test_is_push_prone_line():
    assert is_push_prone_line(3.0)
    assert not is_push_prone_line(3.5)


def test_exact_spread_hedge_requires_opposite_points():
    token = Outcome("Boston Celtics", 1.91, -2.5)
    hedge = Outcome("Los Angeles Lakers", 1.93, 2.5)
    bad = Outcome("Los Angeles Lakers", 1.93, 3.5)
    assert is_exact_spread_hedge(token, hedge)
    assert not is_exact_spread_hedge(token, bad)


def test_exact_total_hedge_requires_same_point():
    over = Outcome("Over", 1.91, 221.5)
    under = Outcome("Under", 1.92, 221.5)
    wrong = Outcome("Under", 1.92, 222.5)
    assert is_exact_total_hedge(over, under)
    assert not is_exact_total_hedge(over, wrong)


def test_build_spread_and_total_legs():
    game = _nba_game()
    spread = build_spread_leg(
        game,
        Outcome("Boston Celtics", 1.91, -2.5),
        "fanduel",
        ("draftkings",),
    )
    total = build_total_leg(
        game,
        Outcome("Over", 1.91, 221.5),
        "fanduel",
        ("draftkings",),
    )
    assert spread is not None
    assert total is not None
    assert spread.token_point == -2.5
    assert spread.hedge_point == 2.5
    assert format_selection("totals", "Over", 221.5) == "Over 221.5"


def test_integer_spread_line_is_excluded():
    game = _nba_game()
    leg = build_spread_leg(
        game,
        Outcome("Boston Celtics", 1.91, -3.0),
        "fanduel",
        ("draftkings",),
    )
    assert leg is None


def test_dry_run_finds_spread_plan():
    client = OddsApiClient(dry_run=True)
    games = client.get_odds("basketball_nba", markets="h2h,spreads,totals")
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=25,
        boost_pct=0.40,
        sport_key="basketball_nba",
    )
    plans = find_best_plans(
        games,
        token,
        leg_count=1,
        hedge_books=("draftkings",),
        bankroll=500,
        market_key="spreads",
    )
    assert plans
    assert plans[0].legs[0].market_key == "spreads"


def test_mixed_market_candidates_include_multiple_types():
    client = OddsApiClient(dry_run=True)
    games = client.get_odds("basketball_nba", markets="h2h,spreads,totals")
    candidates = enumerate_candidate_legs(
        games,
        "fanduel",
        ("draftkings",),
        "h2h,spreads,totals",
    )
    market_types = {leg.market_key for leg in candidates}
    assert "h2h" in market_types
    assert "spreads" in market_types
    assert "totals" in market_types
