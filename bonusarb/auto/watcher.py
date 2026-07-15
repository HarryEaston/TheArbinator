"""Live resolution watcher for auto-hedged parlay legs.

We watch the *hedge* token (the Polymarket outcome we bought shares of, which is
the opposite of the token-side selection). Its share price pins the leg state:

- hedge price -> 1.0  => opposite won => token-side selection LOST => parlay dead
- hedge price -> 0.0  => opposite lost => token-side selection WON  => parlay alive

Asymmetric thresholds load risk onto the safer direction:

- **lost_threshold** (default 0.97): declare LOST when hedge price >= this.
  Wrong stop only misses later hedges (forgone profit).
- **won_threshold** (default 0.99): declare WON when hedge price <= 1 - this
  (i.e. <= 0.01). Wrong advance places an unhedged next-leg bet, so we demand
  higher certainty before placing the next hedge.
"""

from __future__ import annotations

from dataclasses import dataclass

from bonusarb.polymarket.client import PolymarketClient


PENDING = "pending"
WON = "won"
LOST = "lost"


@dataclass(frozen=True)
class LegResolution:
    status: str  # PENDING | WON | LOST
    hedge_price: float | None


class ResolutionWatcher:
    def __init__(
        self,
        client: PolymarketClient,
        *,
        won_threshold: float = 0.99,
        lost_threshold: float = 0.97,
    ) -> None:
        self.client = client
        self.won_threshold = won_threshold
        self.lost_threshold = lost_threshold

    def resolve(self, hedge_token_id: str | None) -> LegResolution:
        if not hedge_token_id:
            return LegResolution(PENDING, None)
        price = self.client.get_token_price(hedge_token_id, side="BUY", force_fetch=True)
        if price is None:
            return LegResolution(PENDING, None)
        return self._classify(price)

    def _classify(self, hedge_price: float) -> LegResolution:
        if hedge_price >= self.lost_threshold:
            return LegResolution(LOST, hedge_price)
        if hedge_price <= 1.0 - self.won_threshold:
            return LegResolution(WON, hedge_price)
        return LegResolution(PENDING, hedge_price)
