"""Tests for classic two-way arb sizing and natural sportsbook stakes."""

from bonusarb.arb.two_way import (
    arb_edge,
    is_natural_stake,
    locked_profit_for_stakes,
    size_two_way_arb,
    size_two_way_arb_continuous,
)


def test_arb_edge_positive_when_combined_implied_under_one():
    # 2.10 / 2.10 => implied 0.476+0.476 = 0.952 => edge ~4.8%
    edge = arb_edge(2.10, 2.10)
    assert edge > 0.04
    assert abs(edge - (1.0 - 1.0 / 2.10 - 1.0 / 2.10)) < 1e-9


def test_arb_edge_negative_with_vig():
    assert arb_edge(1.91, 1.91) < 0


def test_continuous_sizing_equalizes_returns():
    sized = size_two_way_arb_continuous(2.10, 2.05, 100.0)
    assert sized is not None
    assert abs(sized.stake_a + sized.stake_b - 100.0) < 1e-9
    ret_a = sized.stake_a * 2.10
    ret_b = sized.stake_b * 2.05
    assert abs(ret_a - ret_b) < 1e-6
    assert sized.locked_profit > 0
    assert abs(sized.roi - sized.locked_profit / 100.0) < 1e-9


def test_continuous_rejects_no_arb():
    assert size_two_way_arb_continuous(1.91, 1.91, 100.0) is None


def test_is_natural_stake():
    assert is_natural_stake(35)
    assert is_natural_stake(65)
    assert is_natural_stake(40)
    assert is_natural_stake(22)
    assert is_natural_stake(11)
    assert is_natural_stake(111)
    assert not is_natural_stake(42)
    assert not is_natural_stake(57)
    assert not is_natural_stake(0)
    assert not is_natural_stake(-5)


def test_book_vs_book_prefers_natural_pair_over_continuous_cents():
    # Strong arb so a natural pair summing to 100 stays +EV after rounding.
    sized = size_two_way_arb(
        2.30,
        2.00,
        100.0,
        book_a="fanduel",
        book_b="draftkings",
    )
    assert sized is not None
    assert sized.stake_a == int(sized.stake_a)
    assert sized.stake_b == int(sized.stake_b)
    assert is_natural_stake(int(sized.stake_a))
    assert is_natural_stake(int(sized.stake_b))
    assert abs(sized.stake_a + sized.stake_b - 100.0) < 1e-9
    assert sized.locked_profit > 0
    # Continuous would be ~46.5 / 53.5; we must not return those fractions.
    assert sized.stake_a != 46.5
    assert abs(sized.stake_a - round(sized.stake_a)) < 1e-9


def test_book_vs_book_35_65_style_pair_exists_for_even_odds():
    sized = size_two_way_arb(
        2.30,
        2.00,
        100.0,
        book_a="fanduel",
        book_b="draftkings",
    )
    assert sized is not None
    assert sized.stake_a + sized.stake_b == 100.0
    locked = locked_profit_for_stakes(
        sized.stake_a, 2.30, sized.stake_b, 2.00
    )
    assert locked > 0
    assert abs(locked - sized.locked_profit) < 1e-9
    # Example natural split near continuous ~46.5/53.5 is 45/55.
    assert (sized.stake_a, sized.stake_b) == (45.0, 55.0) or (
        is_natural_stake(int(sized.stake_a)) and is_natural_stake(int(sized.stake_b))
    )


def test_book_vs_polymarket_rounds_only_sportsbook_side():
    sized = size_two_way_arb(
        2.10,
        2.05,
        100.0,
        book_a="fanduel",
        book_b="polymarket",
    )
    assert sized is not None
    assert is_natural_stake(int(sized.stake_a))
    # Polymarket side may be fractional.
    assert sized.stake_b == 100.0 - sized.stake_a
    assert sized.locked_profit > 0


def test_polymarket_as_side_a_rounds_sportsbook_side_b():
    sized = size_two_way_arb(
        2.10,
        2.05,
        100.0,
        book_a="polymarket",
        book_b="draftkings",
    )
    assert sized is not None
    assert is_natural_stake(int(sized.stake_b))
    assert abs(sized.stake_a + sized.stake_b - 100.0) < 1e-9
