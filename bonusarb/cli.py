"""Command-line interface for Bonus Token Arbitrage Finder.

Scans NHL / NFL / MLB / NBA boards on FanDuel and DraftKings for single-leg
profit-boost token opportunities, automatically pulls Polymarket hedge odds for
every matched game, and sizes a hedge that locks in profit.
"""

from __future__ import annotations

import argparse

from bonusarb.config import (
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_MIN_GAP_MINUTES,
    LEAGUES,
    ODDS_API_KEY,
)
from bonusarb.models import TOKEN_BOOKS
from bonusarb.oddsapi.client import OddsApiClient
from bonusarb.oddsapi.cache import OddsCache
from bonusarb.runners import (
    RunConfig,
    collect_interactive_config,
    config_from_args,
    missing_batch_fields,
    run_auto,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find single-leg profit-boost token arbitrage for NHL/NFL/MLB/NBA.",
        epilog="Run without flags for a guided step-by-step prompt.",
    )
    parser.add_argument(
        "--sport",
        choices=[key for key, _ in LEAGUES],
        help="League key, e.g. basketball_nba",
    )
    parser.add_argument(
        "--legs",
        type=int,
        choices=[1, 2, 3],
        default=None,
        help="Number of parlay legs (1-3; default 3 in --batch mode, prompted otherwise)",
    )
    parser.add_argument("--token-book", choices=list(TOKEN_BOOKS), help="Book with the bonus token")
    parser.add_argument("--boost", type=float, help="Profit boost as decimal (0-5); 0 = standard arbitrage, e.g. 0.30")
    parser.add_argument("--max-stake", type=float, help="Maximum token stake")
    parser.add_argument("--bankroll", type=float, help="Optional hedge bankroll cap (unlimited if omitted)")
    parser.add_argument("--min-gap-minutes", type=int, default=DEFAULT_MIN_GAP_MINUTES)
    parser.add_argument(
        "--market",
        default=None,
        help="Market key(s), comma-separated: h2h, spreads, totals (default: all)",
    )
    parser.add_argument("--cache-ttl", type=int, default=DEFAULT_CACHE_TTL_SECONDS)
    parser.add_argument("--api-key", default=ODDS_API_KEY, help="Odds API key")
    parser.add_argument("--no-fetch", action="store_true", help="Use cache only")
    parser.add_argument("--dry-run", action="store_true", help="Use bundled sample odds data")
    parser.add_argument("--recheck", action="store_true", help="Force refresh odds before solving")
    parser.add_argument("--save", action="store_true", help="Save the top plan to history/")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Non-interactive mode: all required flags must be provided",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Force step-by-step prompts even when flags are provided",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    use_interactive = _should_use_interactive(args)
    cache = OddsCache(ttl_seconds=args.cache_ttl)
    client = OddsApiClient(api_key=args.api_key, cache=cache, dry_run=args.dry_run)

    if use_interactive:
        config = collect_interactive_config(args, client)
    else:
        missing = missing_batch_fields(args)
        if missing:
            print("Batch mode requires: " + ", ".join(missing))
            print("Run without --batch for guided prompts, or pass the missing flags.")
            return 1
        config = config_from_args(args)

    assert isinstance(config, RunConfig)
    if config.dry_run:
        client = OddsApiClient(api_key=config.api_key, cache=cache, dry_run=True)
    return run_auto(config, client, cache)


def _should_use_interactive(args: argparse.Namespace) -> bool:
    if args.batch:
        return False
    if args.interactive:
        return True
    # Interactive by default; use --batch for fully non-interactive runs.
    return True
