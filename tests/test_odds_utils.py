"""Tests for odds utilities."""

from datetime import datetime

from bonusarb.models import BookmakerOdds, Game, Market, Outcome
from bonusarb.odds_utils import (
    american_to_decimal,
    build_leg,
    decimal_to_american,
    implied_probability,
    is_two_way_market,
)


def test_american_to_decimal_positive():
    assert round(american_to_decimal(150), 2) == 2.50


def test_american_to_decimal_negative():
    assert round(american_to_decimal(-200), 2) == 1.50


def test_decimal_to_american_round_trip():
    assert decimal_to_american(american_to_decimal(130)) == 130


def test_implied_probability():
    assert round(implied_probability(2.0), 2) == 0.50


def test_nba_two_way_h2h_builds_match_winner_leg():
    game = Game(
        id="lakers-celtics",
        sport_key="basketball_nba",
        sport_title="NBA",
        commence_time=datetime.fromisoformat("2026-03-01T23:00:00+00:00"),
        home_team="Celtics",
        away_team="Lakers",
        bookmakers={
            "fanduel": BookmakerOdds(
                key="fanduel",
                title="FanDuel",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(
                            Outcome("Lakers", 3.1),
                            Outcome("Celtics", 1.45),
                        ),
                    )
                },
            ),
            "draftkings": BookmakerOdds(
                key="draftkings",
                title="DraftKings",
                markets={
                    "h2h": Market(
                        key="h2h",
                        outcomes=(
                            Outcome("Lakers", 3.2),
                            Outcome("Celtics", 1.43),
                        ),
                    )
                },
            ),
        },
    )

    leg = build_leg(game, "Lakers", "fanduel", ("draftkings",))

    assert is_two_way_market(game, "fanduel")
    assert leg is not None
    assert leg.selection == "Lakers"
    assert leg.opposite_selection == "Celtics"
    assert leg.hedge_book == "draftkings"
    assert leg.hedge_odds == 1.43
