"""CLI for the ``auto`` subcommand.

Usage:
    python -m bonusarb auto [scan-flags...] [--live | --paper] [--resume [PARLAY_ID]]
    python -m bonusarb auto --list

Mirrors the scan flags of the default command (so the same interactive prompts
or batch flags select the same parlays), then filters to fully
Polymarket-hedgeable plans, lets you pick one, confirms the sportsbook bet is
placed by hand, and hands off to the monitor loop.

Paper mode is the default: it simulates fills and never touches the CLOB SDK or
your wallet. Pass ``--live`` to arm real order placement.

Each run only tracks a single parlay, but nothing stops you from running
several ``auto`` processes at once (one per terminal) to hedge multiple
parlays in parallel. ``--list`` shows every in-progress parlay's id so a
crashed/closed terminal can be resumed with ``--resume PARLAY_ID`` instead of
only ever resuming the most recent one.
"""

from __future__ import annotations

import argparse
import sys
import time

from bonusarb.auto.config import AutoConfig
from bonusarb.auto.executor import ClobExecutor
from bonusarb.auto.monitor import run_monitor
from bonusarb.auto.notify import TelegramNotifier
from bonusarb.auto.select import (
    confirm_sportsbook_bet_placed,
    prompt_pick_plan,
    render_automatable,
    scan_automatable_plans,
)
from bonusarb.auto.state import (
    ActiveParlay,
    latest_parlay_path,
    list_in_progress_parlays,
    load_parlay,
    parlay_path_for_id,
)
from bonusarb.auto.watcher import ResolutionWatcher
from bonusarb.config import (
    AUTO_LOST_THRESHOLD,
    AUTO_MAX_SLIPPAGE,
    AUTO_MIN_LOCKED_PROFIT_FLOOR,
    AUTO_POLL_INTERVAL_SECONDS,
    AUTO_WON_THRESHOLD,
    DEFAULT_CACHE_TTL_SECONDS,
    DEFAULT_MIN_GAP_MINUTES,
    LEAGUES,
    ODDS_API_KEY,
)
from bonusarb.fx import FxError
from bonusarb.models import TOKEN_BOOK_CLI_CHOICES, TokenConstraint, TokenType
from bonusarb.oddsapi.cache import OddsCache
from bonusarb.oddsapi.client import OddsApiClient
from bonusarb.runners import (
    MAX_LEG_COUNT,
    MIN_LEG_COUNT,
    RunConfig,
    add_stake_currency_args,
    collect_interactive_config,
    config_from_args,
    missing_batch_fields,
)


def build_auto_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m bonusarb auto",
        description=(
            "Auto-place sequential Polymarket hedges for a picked profit-boost "
            "parlay and live-track each leg's resolution."
        ),
    )
    # --- Scan flags (mirror the default command) ---
    parser.add_argument("--sport", choices=[key for key, _ in LEAGUES])
    parser.add_argument(
        "--legs", type=int, choices=list(range(MIN_LEG_COUNT, MAX_LEG_COUNT + 1)), default=None
    )
    parser.add_argument(
        "--token-book",
        choices=list(TOKEN_BOOK_CLI_CHOICES),
        help="Book with the bonus token (espnbet / thescore = theScore Bet)",
    )
    parser.add_argument("--boost", type=float, help="Profit boost decimal (0-5); 0 = standard arb")
    parser.add_argument("--max-stake", type=float)
    parser.add_argument("--bankroll", type=float)
    parser.add_argument("--min-gap-minutes", type=int, default=DEFAULT_MIN_GAP_MINUTES)
    parser.add_argument("--market", default=None, help="h2h,spreads,totals (default: all)")
    parser.add_argument("--cache-ttl", type=int, default=DEFAULT_CACHE_TTL_SECONDS)
    parser.add_argument("--api-key", default=ODDS_API_KEY)
    parser.add_argument("--no-fetch", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--recheck", action="store_true")
    parser.add_argument("--batch", action="store_true")
    parser.add_argument("--interactive", action="store_true")
    add_stake_currency_args(parser)

    # --- Auto-specific flags ---
    parser.add_argument(
        "--live",
        action="store_true",
        help="Place real Polymarket orders (requires wallet creds in .env).",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Simulate fills without placing orders (default).",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="__latest__",
        default=None,
        metavar="PARLAY_ID",
        help=(
            "Resume an in-progress parlay instead of scanning. With no id, "
            "resumes the most recently created one in state/; pass a specific "
            "id (see `auto --list`) to resume one of several parlays running "
            "at once."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List in-progress (active/paused) parlays saved in state/, then exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify wallet connectivity + signature/funder setup, then exit.",
    )
    parser.add_argument("--poll-interval", type=float, default=AUTO_POLL_INTERVAL_SECONDS)
    parser.add_argument("--profit-floor", type=float, default=AUTO_MIN_LOCKED_PROFIT_FLOOR)
    parser.add_argument("--slippage", type=float, default=AUTO_MAX_SLIPPAGE)
    parser.add_argument(
        "--won-threshold",
        type=float,
        default=AUTO_WON_THRESHOLD,
        help="Declare leg WON (place next hedge) when hedge price <= 1 - this (default 0.99)",
    )
    parser.add_argument(
        "--lost-threshold",
        type=float,
        default=AUTO_LOST_THRESHOLD,
        help="Declare leg LOST (stop) when hedge price >= this (default 0.97)",
    )
    return parser


def auto_main(argv: list[str]) -> int:
    parser = build_auto_parser()
    args = parser.parse_args(argv)

    auto_config = AutoConfig.from_env()
    # CLI flags override env for the safety knobs.
    auto_config.min_locked_profit_floor = args.profit_floor
    auto_config.max_slippage = args.slippage
    auto_config.poll_interval_seconds = args.poll_interval
    auto_config.won_threshold = args.won_threshold
    auto_config.lost_threshold = args.lost_threshold

    paper = _resolve_paper_mode(args, auto_config)
    notifier = TelegramNotifier(auto_config.telegram_bot_token, auto_config.telegram_chat_id)

    if args.check:
        return _run_wallet_check(auto_config)

    if args.list:
        return _run_list_parlays()

    if args.resume is not None:
        parlay = _load_resume_target(args.resume)
        if parlay is None:
            return 1
        print(f"Resuming parlay {parlay.id} (status={parlay.status}).")
    else:
        parlay = _select_and_confirm(args, auto_config)
        if parlay is None:
            return 1

    cache = OddsCache(ttl_seconds=args.cache_ttl)
    polymarket_client = _build_polymarket_client(args, cache)
    executor = ClobExecutor(
        auto_config,
        polymarket_client,
        paper=paper,
    )
    watcher = ResolutionWatcher(
        polymarket_client,
        won_threshold=auto_config.won_threshold,
        lost_threshold=auto_config.lost_threshold,
    )

    print(
        f"Starting monitor loop ({'paper' if paper else 'LIVE'} mode, "
        f"poll every {auto_config.poll_interval_seconds:.0f}s). Ctrl+C to stop; "
        f"re-run with `--resume {parlay.id}` to continue."
    )
    try:
        return _run_loop(parlay, executor, watcher, notifier, auto_config)
    except KeyboardInterrupt:
        print(
            "\nInterrupted. State saved; re-run "
            f"`python -m bonusarb auto --resume {parlay.id}` to continue."
        )
        return 130


def _resolve_paper_mode(args: argparse.Namespace, auto_config: AutoConfig) -> bool:
    if args.live and args.paper:
        print("Pass either --live or --paper, not both.")
        raise SystemExit(2)
    if args.live:
        if not auto_config.live_enabled:
            print(
                "--live requires POLYMARKET_PRIVATE_KEY and POLYMARKET_FUNDER_ADDRESS in .env."
            )
            raise SystemExit(2)
        return False
    return True  # paper is the default


def _run_wallet_check(auto_config: AutoConfig) -> int:
    if not auto_config.live_enabled:
        print(
            "No wallet credentials in .env (POLYMARKET_PRIVATE_KEY / "
            "POLYMARKET_FUNDER_ADDRESS). Add them and re-run `auto --check`."
        )
        return 1
    from bonusarb.auto.executor import ClobOrderApi

    api = ClobOrderApi(auto_config)
    try:
        info = api.check_wallet()
    except Exception as exc:
        print(f"Wallet check failed: {exc}")
        msg = str(exc).lower()
        if "signature" in msg or "funder" in msg or "signer" in msg:
            print(
                "If this is a signature error, try POLYMARKET_SIGNATURE_TYPE=1 "
                "(POLY_PROXY) instead of 2 (POLY_GNOSIS_SAFE), or vice versa."
            )
        return 1
    print("Wallet check OK.")
    print(f"  signer address: {info.get('signer')}")
    print(f"  signature_type: {auto_config.signature_type}")
    print(f"  funder:         {auto_config.funder_address}")
    print(f"  USDC balance/allowance: {info.get('balance_allowance')}")
    print(
        "Confirm the signer/funder match your Polymarket deposit address and "
        "that the USDC balance covers your planned hedges before using --live."
    )
    return 0


def _run_list_parlays() -> int:
    parlays = list_in_progress_parlays()
    if not parlays:
        print("No in-progress parlays found in state/.")
        return 0
    print("In-progress parlays:")
    for parlay in parlays:
        placed = len(parlay.placed_legs())
        total = len(parlay.legs)
        print(
            f"  {parlay.id}  status={parlay.status}  legs={placed}/{total} placed  "
            f"sport={parlay.sport_key}  stake={parlay.stake:.2f}"
        )
    print("\nResume a specific one with: python -m bonusarb auto --resume <PARLAY_ID>")
    return 0


def _load_resume_target(resume_arg: str) -> ActiveParlay | None:
    if resume_arg == "__latest__":
        path = latest_parlay_path()
        if path is None:
            print("No in-progress parlay found in state/ to resume.")
            return None
        return load_parlay(path)
    path = parlay_path_for_id(resume_arg)
    if not path.exists():
        print(
            f"No parlay found with id {resume_arg!r} in state/. "
            "Run `python -m bonusarb auto --list` to see active parlays."
        )
        return None
    return load_parlay(path)


def _select_and_confirm(args: argparse.Namespace, auto_config: AutoConfig) -> ActiveParlay | None:
    use_interactive = _should_use_interactive(args)
    cache = OddsCache(ttl_seconds=args.cache_ttl)
    client = OddsApiClient(api_key=args.api_key, cache=cache, dry_run=args.dry_run)

    try:
        if use_interactive:
            config = collect_interactive_config(args, client)
        else:
            missing = missing_batch_fields(args)
            if missing:
                print("Batch mode requires: " + ", ".join(missing))
                return None
            config = config_from_args(args)
    except FxError as exc:
        print(f"FX error: {exc}")
        return None
    if config.dry_run:
        client = OddsApiClient(api_key=config.api_key, cache=cache, dry_run=True)

    plans, warnings = scan_automatable_plans(config, client, cache)
    token = _token_from_config(config)
    render_automatable(plans, token, client.quota, warnings, cad_rate=config.display_cad_rate)
    if not plans:
        return None

    plan = prompt_pick_plan(plans, cad_rate=config.display_cad_rate)
    if plan is None:
        return None
    if not confirm_sportsbook_bet_placed(plan, cad_rate=config.display_cad_rate):
        print("Aborting: sportsbook bet not confirmed placed.")
        return None

    parlay = ActiveParlay.from_plan(
        plan,
        sport_key=config.sport_key,
        usd_cad_rate=config.display_cad_rate,
    )
    path = parlay.save()
    print(f"Saved active parlay state to {path}")
    return parlay


def _run_loop(
    parlay: ActiveParlay,
    executor: ClobExecutor,
    watcher: ResolutionWatcher,
    notifier: TelegramNotifier,
    auto_config: AutoConfig,
) -> int:
    while parlay.status == "active":
        parlay = run_monitor(parlay, executor, watcher, notifier, auto_config)
        if parlay.status in {"complete", "paused"}:
            break
        # Still active with a pending watch -> wait and retry.
        time.sleep(auto_config.poll_interval_seconds)

    print(f"\nFinal status: {parlay.status}")
    if parlay.summary:
        print(parlay.summary)
    return 0 if parlay.status == "complete" else 1


def _should_use_interactive(args: argparse.Namespace) -> bool:
    if args.batch:
        return False
    if args.interactive:
        return True
    return True


def _token_from_config(config: RunConfig) -> TokenConstraint:
    return TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book=config.token_book,
        min_legs=config.min_legs,
        max_stake=config.max_stake,
        boost_pct=config.boost_pct,
        sport_key=config.sport_key,
    )


def _build_polymarket_client(args: argparse.Namespace, cache: OddsCache):
    from bonusarb.polymarket.client import PolymarketClient

    return PolymarketClient(cache=cache, dry_run=args.dry_run)
