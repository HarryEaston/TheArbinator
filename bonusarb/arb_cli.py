"""CLI for the ``arb`` subcommand — general 1-leg all-pairs arbitrage.

Usage:
    python -m bonusarb arb
    python -m bonusarb arb --batch --dry-run --usd --capital 100
    python -m bonusarb arb --sport basketball_nba --capital 100 --usd
"""

from __future__ import annotations

import argparse
import sys

from bonusarb.config import (
    ARB_LEAGUES,
    DEFAULT_ARB_CAPITAL,
    DEFAULT_ARB_TOP,
    DEFAULT_CACHE_TTL_SECONDS,
    ODDS_API_KEY,
)
from bonusarb.fx import FxError
from bonusarb.oddsapi.cache import OddsCache
from bonusarb.oddsapi.client import OddsApiClient
from bonusarb.runners import (
    add_stake_currency_args,
    arb_config_from_args,
    collect_arb_interactive_config,
    run_arb,
)


def build_arb_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bonusarb arb",
        description=(
            "Find 1-leg arbitrage across FanDuel, DraftKings, BetMGM, "
            "theScore Bet, and Polymarket (no boost tokens). Scans NHL, NFL, "
            "MLB, NBA, and WNBA by default."
        ),
        epilog="Run without flags for guided prompts (capital defaults to 100).",
    )
    parser.add_argument(
        "--sport",
        choices=[key for key, _ in ARB_LEAGUES],
        help="Restrict to one league (default: all arb leagues)",
    )
    parser.add_argument(
        "--capital",
        type=float,
        default=None,
        help=f"Total capital to split across both sides (default: {DEFAULT_ARB_CAPITAL:g})",
    )
    parser.add_argument(
        "--market",
        default=None,
        help="Market key(s), comma-separated: h2h, spreads, totals (default: all)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=None,
        help=f"Max opportunities to show (default: {DEFAULT_ARB_TOP})",
    )
    parser.add_argument("--cache-ttl", type=int, default=DEFAULT_CACHE_TTL_SECONDS)
    parser.add_argument("--api-key", default=ODDS_API_KEY, help="Odds API key")
    parser.add_argument("--no-fetch", action="store_true", help="Use cache only")
    parser.add_argument("--dry-run", action="store_true", help="Use bundled sample odds")
    parser.add_argument("--recheck", action="store_true", help="Force refresh odds")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Non-interactive mode (uses defaults for omitted flags)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Force step-by-step prompts even when flags are provided",
    )
    add_stake_currency_args(parser)
    return parser


def arb_main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    # When routed from ``python -m bonusarb arb ...``, callers pass argv without
    # the leading ``arb`` token.
    parser = build_arb_parser()
    args = parser.parse_args(raw_argv)

    use_interactive = _should_use_interactive(args)
    cache = OddsCache(ttl_seconds=args.cache_ttl)
    client = OddsApiClient(api_key=args.api_key, cache=cache, dry_run=args.dry_run)

    if use_interactive:
        try:
            config = collect_arb_interactive_config(args, client)
        except FxError as exc:
            print(f"FX error: {exc}")
            return 1
    else:
        try:
            config = arb_config_from_args(args)
        except FxError as exc:
            print(f"FX error: {exc}")
            return 1

    if config.dry_run:
        client = OddsApiClient(api_key=config.api_key, cache=cache, dry_run=True)
    return run_arb(config, client, cache)


def _should_use_interactive(args: argparse.Namespace) -> bool:
    if args.batch:
        return False
    if args.interactive:
        return True
    return True
