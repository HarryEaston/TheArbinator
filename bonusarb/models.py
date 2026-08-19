"""Core data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


BookmakerKey = Literal["fanduel", "draftkings", "betmgm", "espnbet", "polymarket"]

# Books that can hold a bonus token. Polymarket is a prediction market, not a
# sportsbook, so bonus tokens never live there; it is hedge-only.
# ``espnbet`` is The Odds API key for theScore Bet (formerly ESPN Bet).
TOKEN_BOOKS: tuple[BookmakerKey, ...] = ("fanduel", "draftkings", "betmgm", "espnbet")

# CLI aliases accepted by ``--token-book`` (normalized to TOKEN_BOOKS keys).
TOKEN_BOOK_CLI_CHOICES: tuple[str, ...] = TOKEN_BOOKS + (
    "thescore",
    "thescorebet",
    "the_score",
)


class TokenType(str, Enum):
    PROFIT_BOOST = "profit_boost"


@dataclass(frozen=True)
class Outcome:
    name: str
    price: float
    point: float | None = None
    # Polymarket CLOB token id for this outcome. Only set on outcomes sourced
    # from the synthetic "polymarket" bookmaker; needed to place/watch orders.
    token_id: str | None = None
    # ``clob`` or ``gamma_fallback`` when sourced from Polymarket.
    price_source: str | None = None


@dataclass(frozen=True)
class Market:
    key: str
    outcomes: tuple[Outcome, ...]
    last_update: datetime | None = None


@dataclass(frozen=True)
class BookmakerOdds:
    key: BookmakerKey
    title: str
    markets: dict[str, Market]


@dataclass(frozen=True)
class Game:
    id: str
    sport_key: str
    sport_title: str
    commence_time: datetime
    home_team: str
    away_team: str
    bookmakers: dict[BookmakerKey, BookmakerOdds]


@dataclass(frozen=True)
class Leg:
    game_id: str
    event_label: str
    commence_time: datetime
    selection: str
    opposite_selection: str
    token_book: BookmakerKey
    token_odds: float
    hedge_book: BookmakerKey
    hedge_odds: float
    market_key: str = "h2h"
    token_point: float | None = None
    hedge_point: float | None = None
    # Polymarket execution context. Only populated when hedge_book == "polymarket"
    # so the auto-hedger can place and watch the hedge without re-discovering it.
    polymarket_event_slug: str | None = None
    hedge_token_id: str | None = None
    # ``clob`` vs ``gamma_fallback`` for the hedge quote (actionable only if clob).
    hedge_price_source: str | None = None


@dataclass(frozen=True)
class TokenConstraint:
    token_type: TokenType
    token_book: BookmakerKey
    min_legs: int
    max_stake: float
    boost_pct: float = 0.0
    boost_cap: float | None = None
    boosted_odds: float | None = None
    min_combined_odds: float | None = None
    max_combined_odds: float | None = None
    sport_key: str | None = None


@dataclass(frozen=True)
class HedgeStep:
    leg_index: int
    event_label: str
    commence_time: datetime
    selection: str
    book: BookmakerKey
    odds: float
    stake: float
    place_by: datetime
    market_key: str = "h2h"
    # Carried from Leg so the auto-hedger can place/watch this hedge directly.
    polymarket_event_slug: str | None = None
    hedge_token_id: str | None = None
    hedge_price_source: str | None = None


@dataclass(frozen=True)
class HedgePlan:
    legs: tuple[Leg, ...]
    token: TokenConstraint
    stake: float
    combined_odds: float
    effective_win_profit: float
    stake_cost: float
    hedge_steps: tuple[HedgeStep, ...]
    locked_profit: float
    max_cash_needed: float
    roi: float
    is_guaranteed: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class QuotaInfo:
    remaining: int | None = None
    used: int | None = None
    last_cost: int | None = None

    def merge(self, other: QuotaInfo) -> QuotaInfo:
        return QuotaInfo(
            remaining=other.remaining if other.remaining is not None else self.remaining,
            used=other.used if other.used is not None else self.used,
            last_cost=other.last_cost if other.last_cost is not None else self.last_cost,
        )


@dataclass(frozen=True)
class TwoWayArb:
    """A simultaneous two-sided (1-leg) arbitrage opportunity."""

    sport_key: str
    game_id: str
    event_label: str
    commence_time: datetime
    market_key: str
    book_a: BookmakerKey
    selection_a: str
    odds_a: float
    point_a: float | None
    stake_a: float
    book_b: BookmakerKey
    selection_b: str
    odds_b: float
    point_b: float | None
    stake_b: float
    capital: float
    locked_profit: float
    roi: float
    edge: float
    # Polymarket CLOB context when either side is polymarket (manual for v1).
    polymarket_event_slug: str | None = None
    token_id_a: str | None = None
    token_id_b: str | None = None
