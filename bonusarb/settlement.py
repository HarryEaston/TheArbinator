"""Settlement-risk filters for auto / actionable plans.

The sequential solver only equalizes first-loss and all-win paths. Sports with
ties, draws, no-contests, voids, or cross-venue rule gaps can break the locked
profit guarantee. For P1 we exclude those from auto rather than modeling every
settlement state.
"""

from __future__ import annotations

from collections.abc import Iterable

from bonusarb.models import HedgePlan


# Sports where moneyline settlement can diverge (ties / draws / NC / cancels).
SETTLEMENT_RISK_SPORT_KEYS: frozenset[str] = frozenset(
    {
        "americanfootball_nfl",
        "mma_mixed_martial_arts",
    }
)

# Auto only hedges binary moneylines; spreads/totals retain push risk.
AUTO_ALLOWED_MARKET_KEYS: frozenset[str] = frozenset({"h2h"})


def settlement_block_reason(
    sport_key: str | None,
    market_keys: Iterable[str],
) -> str | None:
    """Return a human skip reason, or None if settlement is acceptable for auto."""
    if sport_key in SETTLEMENT_RISK_SPORT_KEYS:
        return (
            f"sport {sport_key} has unmodeled settlement states "
            "(ties/draws/no-contest/void); excluded from auto"
        )
    for key in market_keys:
        if key not in AUTO_ALLOWED_MARKET_KEYS:
            return (
                f"market {key} has push/void risk not modeled by the solver; "
                "auto allows h2h only"
            )
    return None


def plan_settlement_ok(plan: HedgePlan, sport_key: str | None = None) -> tuple[bool, str | None]:
    key = sport_key or plan.token.sport_key
    reason = settlement_block_reason(key, (leg.market_key for leg in plan.legs))
    return (reason is None, reason)
