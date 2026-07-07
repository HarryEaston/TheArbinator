"""Tests for token payoff helpers."""

from bonusarb.models import TokenConstraint, TokenType
from bonusarb.odds_utils import decimal_to_american
from bonusarb.tokens import effective_combined_odds, token_payoff


def test_profit_boost_payoff():
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=2,
        max_stake=25,
        boost_pct=0.30,
    )
    stake_cost, win_profit = token_payoff(token, stake=25, combined_odds=4.0)
    assert stake_cost == 25
    assert win_profit == 25 * 3 * 1.30


def test_zero_boost_payoff_is_standard_parlay_win():
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=25,
        boost_pct=0.0,
    )
    stake_cost, win_profit = token_payoff(token, stake=25, combined_odds=4.0)
    assert stake_cost == 25
    assert win_profit == 25 * 3
    assert effective_combined_odds(token, stake=25, combined_odds=4.0) == 4.0


def test_unsupported_token_type_raises():
    import pytest

    token = TokenConstraint(
        token_type="profit_boost",  # type: ignore[arg-type]
        token_book="fanduel",
        min_legs=1,
        max_stake=25,
        boost_pct=0.30,
    )
    # Force a non-profit-boost value to confirm the guard clause fires.
    object.__setattr__(token, "token_type", "not_a_real_token_type")
    with pytest.raises(ValueError):
        token_payoff(token, stake=25, combined_odds=4.0)


def test_profit_boost_effective_odds_for_plus_500_with_40_percent_boost():
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=25,
        boost_pct=0.40,
    )
    effective_odds = effective_combined_odds(token, stake=25, combined_odds=6.0)
    assert round(effective_odds, 2) == 8.00
    assert decimal_to_american(effective_odds) == 700
