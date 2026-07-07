"""Tests for sequential hedge solver."""

from datetime import datetime, timezone

from bonusarb.arb.sequential import solve_sequential_hedge, verify_plan
from bonusarb.models import Leg, TokenConstraint, TokenType


def _leg(
    game_id: str,
    commence: str,
    token_odds: float,
    hedge_odds: float,
    selection: str = "Team A",
    opposite: str = "Team B",
) -> Leg:
    return Leg(
        game_id=game_id,
        event_label=game_id,
        commence_time=datetime.fromisoformat(commence.replace("Z", "+00:00")),
        selection=selection,
        opposite_selection=opposite,
        token_book="fanduel",
        token_odds=token_odds,
        hedge_book="draftkings",
        hedge_odds=hedge_odds,
    )


def test_single_leg_guaranteed_hedge():
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=100,
        boost_pct=0.0,
    )
    legs = (_leg("g1", "2026-07-03T00:00:00Z", token_odds=2.20, hedge_odds=2.10),)
    plan = solve_sequential_hedge(legs, token, stake=100, sport_key="basketball_nba")
    assert plan is not None
    assert plan.locked_profit > 0
    valid, issues = verify_plan(plan)
    assert valid
    assert issues == []


def test_multi_leg_plan_verifies():
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=3,
        max_stake=25,
        boost_pct=0.50,
    )
    legs = (
        _leg("g1", "2026-07-03T00:00:00Z", token_odds=2.10, hedge_odds=1.95),
        _leg("g2", "2026-07-03T06:00:00Z", token_odds=2.05, hedge_odds=1.95),
        _leg("g3", "2026-07-03T11:00:00Z", token_odds=2.08, hedge_odds=1.98),
    )
    plan = solve_sequential_hedge(legs, token, stake=25, sport_key="basketball_nba")
    assert plan is not None
    assert plan.locked_profit > 0
    valid, issues = verify_plan(plan)
    assert valid
    assert issues == []


def test_spread_leg_sequential_plan_verifies():
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=25,
        boost_pct=0.40,
    )
    legs = (
        Leg(
            game_id="g1",
            event_label="Lakers @ Celtics",
            commence_time=datetime.fromisoformat("2026-07-03T00:00:00+00:00"),
            selection="Boston Celtics",
            opposite_selection="Los Angeles Lakers",
            token_book="fanduel",
            token_odds=1.91,
            hedge_book="draftkings",
            hedge_odds=1.93,
            market_key="spreads",
            token_point=-2.5,
            hedge_point=2.5,
        ),
    )
    plan = solve_sequential_hedge(legs, token, stake=25, sport_key="basketball_nba")
    assert plan is not None
    assert plan.locked_profit > 0
    valid, issues = verify_plan(plan)
    assert valid
    assert issues == []
