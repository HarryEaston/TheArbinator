"""Bonus token payoff and constraint helpers."""

from __future__ import annotations

from bonusarb.models import TokenConstraint, TokenType


def base_win_profit(stake: float, combined_odds: float) -> float:
    return stake * (combined_odds - 1.0)


def token_payoff(token: TokenConstraint, stake: float, combined_odds: float) -> tuple[float, float]:
    """Return (stake_cost, win_profit) for a winning leg.

    Only profit-boost tokens are supported. A boost cap is allowed by the math
    but always ``None`` in this tool.
    """
    if token.token_type != TokenType.PROFIT_BOOST:
        raise ValueError(f"Unsupported token type: {token.token_type}")

    base = base_win_profit(stake, combined_odds)
    boost_amount = base * token.boost_pct
    if token.boost_cap is not None:
        boost_amount = min(boost_amount, token.boost_cap)
    return stake, base + boost_amount


def effective_combined_odds(token: TokenConstraint, stake: float, combined_odds: float) -> float:
    stake_cost, win_profit = token_payoff(token, stake, combined_odds)
    if stake_cost <= 0:
        return 1.0 + (win_profit / stake)
    return 1.0 + (win_profit / stake_cost)


def token_constraints_satisfied(
    token: TokenConstraint,
    stake: float,
    combined_odds: float,
    leg_count: int,
    sport_key: str,
) -> tuple[bool, list[str]]:
    issues: list[str] = []
    if leg_count < token.min_legs:
        issues.append(f"Requires at least {token.min_legs} legs, got {leg_count}.")
    if stake > token.max_stake:
        issues.append(f"Stake ${stake:.2f} exceeds max stake ${token.max_stake:.2f}.")
    if token.sport_key and token.sport_key != sport_key:
        issues.append(f"Token is restricted to sport {token.sport_key}.")
    if token.min_combined_odds and combined_odds < token.min_combined_odds:
        issues.append(
            f"Combined odds {combined_odds:.2f} below minimum {token.min_combined_odds:.2f}."
        )
    if token.max_combined_odds and combined_odds > token.max_combined_odds:
        issues.append(
            f"Combined odds {combined_odds:.2f} above maximum {token.max_combined_odds:.2f}."
        )
    return len(issues) == 0, issues
