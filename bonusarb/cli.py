"""Command-line interface for Bonus Token Arbitrage Finder.

Scans NHL / NFL / MLB / NBA / WNBA / UFC boards on FanDuel,
DraftKings, BetMGM, and theScore Bet for single-leg profit-boost token
opportunities, automatically
pulls Polymarket hedge odds for every matched game, and sizes a hedge that
locks in profit.

Run with the ``auto`` subcommand to hand a picked parlay off to the auto-hedger,
which places sequential Polymarket hedges and live-tracks each leg's resolution.
"""

from __future__ import annotations

import argparse
import sys

from bonusarb.config import (
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_MIN_GAP_MINUTES,
    LEAGUES,
    ODDS_API_KEY,
)
from bonusarb.models import TOKEN_BOOK_CLI_CHOICES
from bonusarb.oddsapi.client import OddsApiClient
from bonusarb.oddsapi.cache import OddsCache
from bonusarb.fx import FxError
from bonusarb.runners import (
    MAX_LEG_COUNT,
    MIN_LEG_COUNT,
    RunConfig,
    add_stake_currency_args,
    collect_interactive_config,
    config_from_args,
    missing_batch_fields,
    run_auto,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find single-leg profit-boost token arbitrage for NHL/NFL/MLB/NBA/WNBA/UFC.",
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
        choices=list(range(MIN_LEG_COUNT, MAX_LEG_COUNT + 1)),
        default=None,
        help=(
            f"Number of parlay legs ({MIN_LEG_COUNT}-{MAX_LEG_COUNT}; "
            "default 3 in --batch mode, prompted otherwise)"
        ),
    )
    parser.add_argument(
        "--token-book",
        choices=list(TOKEN_BOOK_CLI_CHOICES),
        help="Book with the bonus token (espnbet / thescore = theScore Bet)",
    )
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
    add_stake_currency_args(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv

    # Route the `auto` subcommand to the auto-hedger without disturbing the
    # existing flat-argparse flow so bare `python -m bonusarb` is unchanged.
    if raw_argv and raw_argv[0] == "auto":
        from bonusarb.auto.cli import auto_main

        return auto_main(raw_argv[1:])

    if raw_argv and raw_argv[0] == "arb":
        from bonusarb.arb_cli import arb_main

        return arb_main(raw_argv[1:])

    parser = build_parser()
    args = parser.parse_args(raw_argv)

    use_interactive = _should_use_interactive(args)
    cache = OddsCache(ttl_seconds=args.cache_ttl)
    client = OddsApiClient(api_key=args.api_key, cache=cache, dry_run=args.dry_run)

    if use_interactive:
        try:
            config = collect_interactive_config(args, client)
        except FxError as exc:
            print(f"FX error: {exc}")
            return 1
    else:
        missing = missing_batch_fields(args)
        if missing:
            print("Batch mode requires: " + ", ".join(missing))
            print("Run without --batch for guided prompts, or pass the missing flags.")
            return 1
        try:
            config = config_from_args(args)
        except FxError as exc:
            print(f"FX error: {exc}")
            return 1

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
