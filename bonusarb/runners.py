"""Run orchestration: build a RunConfig interactively or from CLI flags, then
fetch odds, auto-pull Polymarket hedges, search for the best profit-boost
parlay (1-3 legs), and render the report.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone

from bonusarb.arb.optimizer import find_best_ev_plans, find_best_plans
from bonusarb.config import (
    DEFAULT_BOOKMAKERS,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_CREDIT_WARN_THRESHOLD,
    HISTORY_DIR,
    LEAGUES,
    ODDS_API_KEY,
    available_hedge_books,
)
from bonusarb.models import (
    BookmakerKey,
    TokenConstraint,
    TokenType,
)
from bonusarb.oddsapi.client import OddsApiClient
from bonusarb.oddsapi.cache import OddsCache
from bonusarb.polymarket import PolymarketClient, merge_polymarket_odds
from bonusarb.polymarket.discovery import discover_event_mappings
from bonusarb.prompts import (
    BOOK_LABELS,
    parse_token_book,
    prompt_book_step,
    prompt_data_source,
    prompt_float_step,
    prompt_int_step,
    prompt_league,
    prompt_percent_step,
)
from bonusarb.report import render_report

# Default markets scanned by the automatic optimizer.
DEFAULT_SCAN_MARKETS = "h2h,spreads,totals"

# Parlay leg-count bounds. Most profit-boost tokens require 3 legs; we cap at 3
# because compounded vig beyond that usually eats the boost edge.
DEFAULT_LEG_COUNT = 3
MIN_LEG_COUNT = 1
MAX_LEG_COUNT = 3


@dataclass
class RunConfig:
    sport_key: str
    leg_count: int
    min_legs: int
    token_book: BookmakerKey
    boost_pct: float
    max_stake: float
    bankroll: float | None
    show_ev: bool
    optimize_for: str
    dry_run: bool
    no_fetch: bool
    recheck: bool
    save: bool
    market: str
    min_gap_minutes: int
    cache_ttl: int
    api_key: str


def run_auto(config: RunConfig, client: OddsApiClient, cache: OddsCache) -> int:
    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book=config.token_book,
        min_legs=config.min_legs,
        max_stake=config.max_stake,
        boost_pct=config.boost_pct,
        boost_cap=None,
        boosted_odds=None,
        sport_key=config.sport_key,
    )

    market = config.market or DEFAULT_SCAN_MARKETS
    estimated_cost = 0 if config.dry_run or config.no_fetch else client.estimated_request_cost(market)
    warnings = _build_warnings(config, estimated_cost)

    if (
        not config.dry_run
        and not config.no_fetch
        and client.quota.remaining is not None
    ):
        remaining_after = client.quota.remaining - estimated_cost
        if remaining_after < DEFAULT_CREDIT_WARN_THRESHOLD:
            warnings.append(
                f"After this request (~{estimated_cost} credits) you would have "
                f"~{max(remaining_after, 0)} credits left, below the warning "
                f"threshold ({DEFAULT_CREDIT_WARN_THRESHOLD})."
            )
        if client.would_exceed(estimated_cost):
            print(
                f"Refusing to fetch: estimated cost {estimated_cost} credits exceeds "
                f"the {client.quota.remaining} credits remaining on your API key. "
                "Use --no-fetch, --dry-run, or wait for your quota to reset."
            )
            return 1
    if warnings and not config.dry_run and not config.no_fetch:
        for warning in warnings:
            print(f"Warning: {warning}")

    games = client.get_odds(
        config.sport_key,
        markets=market,
        bookmakers=DEFAULT_BOOKMAKERS,
        allow_fetch=not config.no_fetch,
        force_fetch=config.recheck,
    )
    if not games:
        print("No games with FanDuel/DraftKings odds were returned.")
        return 1

    # Polymarket is always pulled automatically as a hedge venue.
    polymarket_client = PolymarketClient(cache=cache, dry_run=config.dry_run)
    discovered, discovery_warnings = discover_event_mappings(
        config.sport_key,
        games,
        polymarket_client,
        allow_fetch=not config.no_fetch,
        force_fetch=config.recheck,
    )
    warnings.extend(discovery_warnings)
    games, polymarket_warnings = merge_polymarket_odds(
        games,
        discovered,
        polymarket_client,
        allow_fetch=not config.no_fetch,
        force_fetch=config.recheck,
    )
    warnings.extend(polymarket_warnings)

    hedge_books = available_hedge_books(config.token_book)
    plans = find_best_plans(
        games,
        token,
        config.leg_count,
        hedge_books=hedge_books,
        bankroll=config.bankroll,
        min_gap_minutes=config.min_gap_minutes,
        market_key=market,
        optimize_for=config.optimize_for,
    )

    ev_plans: list = []
    if config.show_ev and not plans:
        ev_plans = find_best_ev_plans(
            games,
            token,
            config.leg_count,
            hedge_books=hedge_books,
            bankroll=config.bankroll,
            min_gap_minutes=config.min_gap_minutes,
            market_key=market,
        )

    render_report(plans, token, client.quota, ev_plans=ev_plans, warnings=warnings)

    if config.save and plans:
        _save_plan(plans[0])

    return 0 if plans or ev_plans else 1


def collect_interactive_config(args: argparse.Namespace, client: OddsApiClient) -> RunConfig:
    step = 1

    if args.dry_run or args.no_fetch:
        dry_run, no_fetch = args.dry_run, args.no_fetch
        print(f"(Step {step} skipped: using CLI flags for data source)")
        step += 1
    else:
        dry_run, no_fetch = prompt_data_source(step, 0, args.dry_run, args.no_fetch)
        step += 1

    if dry_run:
        client = OddsApiClient(api_key=args.api_key, cache=client.cache, dry_run=True)

    if args.sport is not None:
        sport_key = args.sport
        print(f"(Step {step} skipped: using --sport {sport_key})")
        step += 1
    else:
        sport_key = prompt_league(step, 0, default=None)
        step += 1

    if args.legs is not None:
        leg_count = args.legs
        print(f"(Step {step} skipped: using --legs {leg_count})")
        step += 1
    else:
        leg_count = prompt_int_step(
            step,
            0,
            "How many legs should the parlay have?",
            minimum=MIN_LEG_COUNT,
            maximum=MAX_LEG_COUNT,
            default=DEFAULT_LEG_COUNT,
        )
        step += 1
    min_legs = leg_count

    if args.token_book is not None:
        token_book = parse_token_book(args.token_book)
        print(f"(Step {step} skipped: using --token-book {BOOK_LABELS[token_book]})")
        step += 1
    else:
        token_book = prompt_book_step(
            step,
            0,
            "Which sportsbook is your bonus token on?",
            default=None,
        )
        step += 1

    if args.boost is not None:
        boost_pct = args.boost
        print(f"(Step {step} skipped: using --boost {boost_pct:.0%})")
        step += 1
    else:
        boost_pct = prompt_percent_step(
            step,
            0,
            "What is the profit boost percentage?",
            default=0.30,
        )
        step += 1

    if args.max_stake is not None:
        max_stake = args.max_stake
        print(f"(Step {step} skipped: using --max-stake ${max_stake:.2f})")
        step += 1
    else:
        max_stake = prompt_float_step(
            step,
            0,
            "What is the maximum token stake ($)?",
            minimum=1.0,
        )
        step += 1

    market = args.market or DEFAULT_SCAN_MARKETS
    bankroll = args.bankroll
    show_ev = False
    optimize_for = "profit"

    recheck = args.recheck
    save = args.save

    print()
    print("Configuration complete. Searching for opportunities...")
    print()

    return RunConfig(
        sport_key=sport_key,
        leg_count=leg_count,
        min_legs=min_legs,
        token_book=token_book,
        boost_pct=boost_pct,
        max_stake=max_stake,
        bankroll=bankroll,
        show_ev=show_ev,
        optimize_for=optimize_for,
        dry_run=dry_run,
        no_fetch=no_fetch,
        recheck=recheck,
        save=save,
        market=market,
        min_gap_minutes=args.min_gap_minutes,
        cache_ttl=args.cache_ttl,
        api_key=args.api_key,
    )


def config_from_args(args: argparse.Namespace) -> RunConfig:
    leg_count = args.legs if args.legs is not None else DEFAULT_LEG_COUNT
    return RunConfig(
        sport_key=args.sport,
        leg_count=leg_count,
        min_legs=leg_count,
        token_book=parse_token_book(args.token_book),
        boost_pct=args.boost if args.boost is not None else 0.0,
        max_stake=args.max_stake,
        bankroll=args.bankroll,
        show_ev=False,
        optimize_for="profit",
        dry_run=args.dry_run,
        no_fetch=args.no_fetch,
        recheck=args.recheck,
        save=args.save,
        market=args.market or DEFAULT_SCAN_MARKETS,
        min_gap_minutes=args.min_gap_minutes,
        cache_ttl=args.cache_ttl,
        api_key=args.api_key,
    )


def missing_batch_fields(args: argparse.Namespace) -> list[str]:
    required = {
        "--sport": args.sport,
        "--token-book": args.token_book,
        "--boost": args.boost,
        "--max-stake": args.max_stake,
    }
    return [flag for flag, value in required.items() if value is None]


def _build_warnings(config: RunConfig, estimated_cost: int) -> list[str]:
    warnings: list[str] = []
    market = config.market or DEFAULT_SCAN_MARKETS
    market_keys = [part.strip() for part in market.split(",")]
    if "spreads" in market_keys or "totals" in market_keys:
        warnings.append(
            "Spreads/totals are restricted to exact opposite-line hedges only. "
            "Whole-number lines are excluded to avoid push risk."
        )
    if estimated_cost > 1:
        warnings.append(f"This request is estimated to cost {estimated_cost} API credits.")
    if config.dry_run:
        warnings.append("Dry-run mode uses bundled sample odds when available.")
    warnings.append("Verify token eligibility and odds manually before placing bets.")
    return warnings


def _save_plan(plan) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = HISTORY_DIR / f"plan_{timestamp}.json"
    payload = {
        "stake": plan.stake,
        "combined_odds": plan.combined_odds,
        "locked_profit": plan.locked_profit,
        "max_cash_needed": plan.max_cash_needed,
        "roi": plan.roi,
        "is_guaranteed": plan.is_guaranteed,
        "legs": [
            {
                "event": leg.event_label,
                "selection": leg.selection,
                "token_odds": leg.token_odds,
            }
            for leg in plan.legs
        ],
        "hedges": [
            {
                "event": step.event_label,
                "selection": step.selection,
                "book": step.book,
                "odds": step.odds,
                "stake": step.stake,
            }
            for step in plan.hedge_steps
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved plan to {path}")


# Re-export for callers that imported these names from the old cli module.
__all__ = [
    "RunConfig",
    "run_auto",
    "collect_interactive_config",
    "config_from_args",
    "missing_batch_fields",
    "DEFAULT_LEG_COUNT",
    "DEFAULT_SCAN_MARKETS",
    "BOOK_LABELS",
    "ODDS_API_KEY",
    "LEAGUES",
    "DEFAULT_CACHE_TTL_SECONDS",
]
