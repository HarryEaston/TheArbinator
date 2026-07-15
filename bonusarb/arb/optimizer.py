"""Leg optimizer for bonus token arbitrage."""

from __future__ import annotations

import itertools
import math

from bonusarb.arb.sequential import solve_sequential_hedge, verify_plan
from bonusarb.models import BookmakerKey, Game, HedgePlan, Leg, TokenConstraint
from bonusarb.odds_utils import combined_decimal_odds, enumerate_game_legs, parse_market_keys
from bonusarb.schedule import combo_has_unique_matchups, is_schedule_feasible
from bonusarb.tokens import token_constraints_satisfied, token_payoff


def enumerate_candidate_legs(
    games: list[Game],
    token_book: BookmakerKey,
    hedge_books: tuple[BookmakerKey, ...],
    market_keys: str | tuple[str, ...] = "h2h",
    *,
    slug_by_game_id: dict[str, str] | None = None,
) -> list[Leg]:
    if isinstance(market_keys, str):
        keys = parse_market_keys(market_keys)
    else:
        keys = market_keys

    candidates: list[Leg] = []
    for game in games:
        candidates.extend(
            enumerate_game_legs(game, token_book, hedge_books, keys, slug_by_game_id=slug_by_game_id)
        )
    return candidates


def _candidate_stakes(token: TokenConstraint, bankroll: float | None) -> list[float]:
    """Stakes to evaluate for the token.

    With no binding cap or bankroll, profit scales linearly and max stake is
    optimal, so only ``max_stake`` is tested. When a boost cap or a bankroll
    limit binds, the optimum may lie below ``max_stake``, so we also sample a
    spread of intermediate stakes (and the bankroll ceiling itself).
    """
    stakes: list[float] = [token.max_stake]

    cap_binds = (
        token.token_type.value == "profit_boost"
        and token.boost_pct > 0
        and token.boost_cap is not None
    )
    bankroll_binds = bankroll is not None and bankroll < token.max_stake

    if cap_binds or bankroll_binds:
        for factor in (0.875, 0.75, 0.625, 0.5, 0.375, 0.25):
            stakes.append(token.max_stake * factor)
    if bankroll_binds and bankroll is not None and bankroll > 0:
        stakes.append(bankroll)

    # Floor to cents and never exceed max_stake. CAD→USD can leave a fractional
    # max_stake (e.g. 21.167); rounding that up to 21.17 would then fail
    # token_constraints_satisfied and wipe every plan.
    unique = sorted(
        {
            min(math.floor(max(1.0, stake) * 100) / 100, token.max_stake)
            for stake in stakes
            if stake > 0
        },
        reverse=True,
    )
    return unique


def find_best_plans(
    games: list[Game],
    token: TokenConstraint,
    leg_count: int,
    hedge_books: tuple[BookmakerKey, ...],
    bankroll: float | None = None,
    min_gap_minutes: int = 30,
    market_key: str = "h2h",
    max_results: int = 5,
    optimize_for: str = "profit",
    *,
    slug_by_game_id: dict[str, str] | None = None,
) -> list[HedgePlan]:
    candidates = enumerate_candidate_legs(
        games, token.token_book, hedge_books, market_key, slug_by_game_id=slug_by_game_id
    )
    if not candidates:
        return []

    sport_key = games[0].sport_key if games else token.sport_key or ""
    game_by_id = {game.id: game for game in games}
    plans: list[HedgePlan] = []

    for combo in itertools.combinations(candidates, leg_count):
        if not combo_has_unique_matchups(combo, game_by_id):
            continue

        feasible, _ = is_schedule_feasible(combo, sport_key, min_gap_minutes)
        if not feasible:
            continue

        combined = combined_decimal_odds(combo)
        for stake in _candidate_stakes(token, bankroll):
            ok, _ = token_constraints_satisfied(token, stake, combined, leg_count, sport_key)
            if not ok:
                continue

            plan = solve_sequential_hedge(
                combo,
                token,
                stake,
                sport_key,
                bankroll=bankroll,
                min_gap_minutes=min_gap_minutes,
            )
            if plan is None:
                continue

            valid, _ = verify_plan(plan)
            if not valid:
                continue

            plans.append(plan)

    plans.sort(key=lambda plan: _plan_sort_key(plan, optimize_for), reverse=True)
    return plans[:max_results]


def _plan_sort_key(plan: HedgePlan, optimize_for: str) -> float:
    if optimize_for == "roi":
        return plan.roi
    return plan.locked_profit


def estimate_ev_plan(
    legs: tuple[Leg, ...],
    token: TokenConstraint,
    stake: float,
    sport_key: str,
) -> HedgePlan | None:
    """Fallback positive-EV estimate when no guaranteed hedge exists."""
    combined = combined_decimal_odds(legs)
    ok, issues = token_constraints_satisfied(token, stake, combined, len(legs), sport_key)
    if not ok:
        return None

    stake_cost, win_profit = token_payoff(token, stake, combined)

    win_probability = 1.0
    for leg in legs:
        sel_imp = 1.0 / leg.token_odds
        opp_imp = 1.0 / leg.hedge_odds
        win_probability *= sel_imp / (sel_imp + opp_imp)

    expected_value = win_probability * win_profit - stake_cost
    if expected_value <= 0:
        return None

    return HedgePlan(
        legs=legs,
        token=token,
        stake=stake,
        combined_odds=combined,
        effective_win_profit=win_profit,
        stake_cost=stake_cost,
        hedge_steps=tuple(),
        locked_profit=expected_value,
        max_cash_needed=stake_cost,
        roi=expected_value / stake_cost if stake_cost > 0 else expected_value / stake,
        is_guaranteed=False,
        warnings=tuple(issues) + ("This is an expected-value estimate, not a guaranteed hedge.",),
    )


def find_best_ev_plans(
    games: list[Game],
    token: TokenConstraint,
    leg_count: int,
    hedge_books: tuple[BookmakerKey, ...],
    bankroll: float | None = None,
    min_gap_minutes: int = 30,
    market_key: str = "h2h",
    max_results: int = 3,
) -> list[HedgePlan]:
    candidates = enumerate_candidate_legs(games, token.token_book, hedge_books, market_key)
    sport_key = games[0].sport_key if games else token.sport_key or ""
    game_by_id = {game.id: game for game in games}
    plans: list[HedgePlan] = []

    for combo in itertools.combinations(candidates, leg_count):
        if not combo_has_unique_matchups(combo, game_by_id):
            continue
        feasible, _ = is_schedule_feasible(combo, sport_key, min_gap_minutes)
        if not feasible:
            continue
        stake = token.max_stake
        if bankroll is not None and bankroll < stake:
            stake = bankroll
        if stake <= 0:
            continue
        plan = estimate_ev_plan(combo, token, stake, sport_key)
        if plan is not None:
            plans.append(plan)

    plans.sort(key=lambda plan: plan.locked_profit, reverse=True)
    return plans[:max_results]
