"""CLI smoke tests for the general arb subcommand."""

from bonusarb.arb_cli import arb_main, build_arb_parser
from bonusarb.config import ARB_LEAGUES


def test_arb_parser_defaults():
    parser = build_arb_parser()
    args = parser.parse_args(["--batch", "--dry-run", "--usd"])
    assert args.batch is True
    assert args.dry_run is True
    assert args.capital is None  # filled from DEFAULT_ARB_CAPITAL in config_from_args
    assert args.sport is None


def test_arb_leagues_exclude_ufc():
    keys = {key for key, _ in ARB_LEAGUES}
    assert "mma_mixed_martial_arts" not in keys
    assert "basketball_nba" in keys
    assert "icehockey_nhl" in keys


def test_arb_dry_run_batch_exits_cleanly():
    # Sample data may or may not contain +EV arbs; exit 0 or 1 both OK if no crash.
    code = arb_main(
        [
            "--batch",
            "--dry-run",
            "--usd",
            "--capital",
            "100",
            "--sport",
            "basketball_nba",
            "--market",
            "h2h",
        ]
    )
    assert code in (0, 1)


def test_main_routes_arb_subcommand():
    from bonusarb.cli import main

    code = main(
        [
            "arb",
            "--batch",
            "--dry-run",
            "--usd",
            "--capital",
            "100",
            "--sport",
            "basketball_nba",
            "--market",
            "h2h",
        ]
    )
    assert code in (0, 1)
