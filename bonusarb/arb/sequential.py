"""Sequential leg-by-leg hedge solver."""

from __future__ import annotations

from datetime import timedelta

from bonusarb.models import HedgePlan, HedgeStep, Leg, TokenConstraint
from bonusarb.odds_utils import combined_decimal_odds, leg_hedge_display
from bonusarb.schedule import expected_finish_time, order_legs_for_hedging
from bonusarb.tokens import token_payoff
from bonusarb.config import DEFAULT_MIN_GAP_MINUTES


def solve_sequential_hedge(
    legs: tuple[Leg, ...],
    token: TokenConstraint,
    stake: float,
    sport_key: str,
    bankroll: float | None = None,
    min_gap_minutes: int = DEFAULT_MIN_GAP_MINUTES,
) -> HedgePlan | None:
    if not legs:
        return None

    ordered = order_legs_for_hedging(legs)
    combined_odds = combined_decimal_odds(ordered)
    stake_cost, win_profit = token_payoff(token, stake, combined_odds)

    hedge_odds = [leg.hedge_odds for leg in ordered]
    if any(odds <= 1.0 for odds in hedge_odds):
        return None

    locked_profit, hedge_stakes = _solve_locked_profit(stake_cost, win_profit, hedge_odds)
    if locked_profit <= 0:
        return None

    max_cash_needed = stake_cost + sum(hedge_stakes)
    if bankroll is not None and max_cash_needed > bankroll:
        return None

    hedge_steps: list[HedgeStep] = []
    for index, (leg, hedge_stake) in enumerate(zip(ordered, hedge_stakes), start=1):
        place_by = expected_finish_time(leg, sport_key) + timedelta(minutes=min_gap_minutes)
        hedge_steps.append(
            HedgeStep(
                leg_index=index,
                event_label=leg.event_label,
                commence_time=leg.commence_time,
                selection=leg_hedge_display(leg),
                book=leg.hedge_book,
                odds=leg.hedge_odds,
                stake=hedge_stake,
                place_by=place_by,
                market_key=leg.market_key,
                polymarket_event_slug=leg.polymarket_event_slug,
                hedge_token_id=leg.hedge_token_id,
            )
        )

    roi = locked_profit / max_cash_needed if max_cash_needed > 0 else 0.0
    return HedgePlan(
        legs=ordered,
        token=token,
        stake=stake,
        combined_odds=combined_odds,
        effective_win_profit=win_profit,
        stake_cost=stake_cost,
        hedge_steps=tuple(hedge_steps),
        locked_profit=locked_profit,
        max_cash_needed=max_cash_needed,
        roi=roi,
        is_guaranteed=True,
    )


def _solve_locked_profit(
    stake_cost: float,
    win_profit: float,
    hedge_odds: list[float],
) -> tuple[float, list[float]]:
    """Solve for locked profit and hedge stakes using closed-form recursion."""
    n = len(hedge_odds)
    if n == 0:
        return win_profit - stake_cost, []

    alphas: list[float] = []
    betas: list[float] = []
    alpha_sum = 0.0
    beta_sum = 0.0

    for odds in hedge_odds:
        margin = odds - 1.0
        alpha_k = (1.0 + alpha_sum) / margin
        beta_k = (beta_sum + stake_cost) / margin
        alphas.append(alpha_k)
        betas.append(beta_k)
        alpha_sum += alpha_k
        beta_sum += beta_k

    denominator = 1.0 + alpha_sum
    locked_profit = (win_profit - beta_sum) / denominator
    hedge_stakes = [alphas[i] * locked_profit + betas[i] for i in range(n)]
    return locked_profit, hedge_stakes


def recompute_remaining_hedges(
    stake_cost: float,
    win_profit: float,
    remaining_hedge_odds: list[float],
    already_placed_stakes: list[float],
) -> tuple[float, list[float]]:
    """Re-size the remaining hedges after line movement, given sunk hedges.

    Once legs 1..k-1 have won and their hedges are placed (at whatever odds
    they filled), the parlay is still alive and we must place hedges k..n at
    *live* odds. The reachable paths are:

      leg i (k <= i <= n) loses: hedge_i*(o_i-1) - sum(prior remaining) - sum_placed - stake_cost
      all win:                  win_profit - sum(remaining) - sum_placed

    (stake_cost is the sportsbook bet, lost on any losing path but returned on
    the all-win path; sum_placed is the cash already spent on hedges 1..k-1,
    which is lost regardless of path since those legs won and their hedges
    expired worthless.)

    Substituting ``stake_cost' = stake_cost + sum_placed`` and
    ``win_profit' = win_profit - sum_placed`` reduces this to the original
    closed-form recursion, which equalizes all remaining paths. Returns
    ``(locked_profit, remaining_stakes)``.
    """
    sum_placed = sum(already_placed_stakes)
    return _solve_locked_profit(
        stake_cost + sum_placed,
        win_profit - sum_placed,
        remaining_hedge_odds,
    )


def verify_plan(plan: HedgePlan) -> tuple[bool, list[str]]:
    """Check that all resolution paths produce approximately the same profit."""
    issues: list[str] = []
    tolerance = 0.05
    n = len(plan.hedge_steps)
    profits: list[float] = []

    for fail_index in range(n):
        profit = plan.hedge_steps[fail_index].stake * (plan.hedge_steps[fail_index].odds - 1.0)
        profit -= sum(step.stake for step in plan.hedge_steps[:fail_index])
        profit -= plan.stake_cost
        profits.append(profit)

    win_profit = plan.effective_win_profit - sum(step.stake for step in plan.hedge_steps)
    profits.append(win_profit)

    target = plan.locked_profit
    for value in profits:
        if abs(value - target) > tolerance:
            issues.append(
                f"Path profit ${value:.2f} differs from locked profit ${target:.2f}."
            )
    return len(issues) == 0, issues
