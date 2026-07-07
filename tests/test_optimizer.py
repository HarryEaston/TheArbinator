"""End-to-end optimizer tests using dry-run sample data."""

from bonusarb.arb.optimizer import find_best_plans
from bonusarb.models import TokenConstraint, TokenType
from bonusarb.oddsapi.client import OddsApiClient


def test_dry_run_finds_guaranteed_plan():
    client = OddsApiClient(dry_run=True)
    games = client.get_odds("basketball_nba", markets="h2h,spreads,totals")
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=25,
        boost_pct=0.50,
        sport_key="basketball_nba",
    )
    plans = find_best_plans(
        games,
        token,
        leg_count=1,
        hedge_books=("draftkings",),
        bankroll=500,
        min_gap_minutes=30,
    )
    assert plans
    assert plans[0].locked_profit > 0
    assert plans[0].is_guaranteed


def test_dry_run_finds_3leg_plan():
    client = OddsApiClient(dry_run=True)
    games = client.get_odds("basketball_nba", markets="h2h,spreads,totals")
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=3,
        max_stake=25,
        boost_pct=0.50,
        sport_key="basketball_nba",
    )
    plans = find_best_plans(
        games,
        token,
        leg_count=3,
        hedge_books=("draftkings",),
        bankroll=500,
        min_gap_minutes=30,
    )
    assert plans
    plan = plans[0]
    assert len(plan.legs) == 3
    assert plan.locked_profit > 0
    assert plan.is_guaranteed
    # Each leg must come from a distinct game.
    leg_events = {leg.event_label for leg in plan.legs}
    assert len(leg_events) == 3
    # Every resolution path (N losers + the all-win path) must lock equal profit.
    assert plan.is_guaranteed


def test_min_legs_rejects_shorter_combo():
    client = OddsApiClient(dry_run=True)
    games = client.get_odds("basketball_nba", markets="h2h,spreads,totals")
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=3,
        max_stake=25,
        boost_pct=0.50,
        sport_key="basketball_nba",
    )
    # Asking for 2 legs while the token requires 3 must yield no plans.
    plans = find_best_plans(
        games,
        token,
        leg_count=2,
        hedge_books=("draftkings",),
        bankroll=500,
        min_gap_minutes=30,
    )
    assert plans == []
