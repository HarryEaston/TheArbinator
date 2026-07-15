"""Tests for live CAD/USD FX helpers."""

from __future__ import annotations

import argparse
import math

import pytest

from bonusarb.fx import (
    FxError,
    cad_to_usd,
    fetch_usd_cad_rate,
    format_hedge_stake,
    format_sportsbook_amount,
    format_usd_amount,
    format_usd_cad,
    usd_to_cad,
)
from bonusarb.runners import apply_cad_stakes, config_from_args


def test_cad_to_usd_and_back():
    rate = 1.42
    cad = 25.0
    usd = cad_to_usd(cad, rate)
    # Floors to the nearest cent so the USD stake never exceeds the CAD budget.
    assert usd == pytest.approx(math.floor(25.0 / 1.42 * 100) / 100)
    assert usd == 17.60
    # Round-trip is within one cent of the original CAD amount.
    assert usd_to_cad(usd, rate) == pytest.approx(cad, abs=0.02)


def test_cad_to_usd_floors_fractional_cents():
    # 30 CAD / 1.417294 ≈ 21.167097; without flooring, optimizer stake rounding
    # would push past max_stake and reject every plan.
    assert cad_to_usd(30.0, 1.417294) == 21.16



def test_format_sportsbook_and_hedge_amounts():
    assert format_sportsbook_amount(10.0, None) == "$10.00 USD"
    assert format_sportsbook_amount(10.0, 1.42) == "$14.20 CAD"
    assert format_usd_amount(33.81) == "$33.81 USD"
    assert format_hedge_stake(33.81, "polymarket", 1.42) == "$33.81 USD"
    assert format_hedge_stake(10.0, "draftkings", 1.42) == "$14.20 CAD"
    text = format_usd_cad(10.0, 1.42)
    assert "$10.00 USD" in text
    assert "$14.20 CAD" in text


def test_fetch_usd_cad_rate_parses_response(monkeypatch):
    class _Resp:
        status_code = 200

        @staticmethod
        def json():
            return {"rates": {"CAD": 1.42}}

        text = ""

    monkeypatch.setattr("bonusarb.fx.requests.get", lambda *a, **k: _Resp())
    assert fetch_usd_cad_rate() == pytest.approx(1.42)


def test_fetch_usd_cad_rate_raises_on_bad_response(monkeypatch):
    class _Resp:
        status_code = 500
        text = "error"

    monkeypatch.setattr("bonusarb.fx.requests.get", lambda *a, **k: _Resp())
    with pytest.raises(FxError):
        fetch_usd_cad_rate()


def test_apply_cad_stakes_converts_by_default_with_manual_rate(capsys):
    args = argparse.Namespace(cad=True, fx_rate=1.42)
    max_usd, bank_usd, rate = apply_cad_stakes(args, 25.0, 100.0)
    assert rate == 1.42
    assert max_usd == pytest.approx(math.floor(25.0 / 1.42 * 100) / 100)
    assert bank_usd == pytest.approx(math.floor(100.0 / 1.42 * 100) / 100)
    out = capsys.readouterr().out
    assert "1.42" in out


def test_apply_cad_stakes_no_conversion_with_usd_flag():
    args = argparse.Namespace(cad=False, fx_rate=None)
    max_usd, bank_usd, rate = apply_cad_stakes(args, 25.0, 100.0)
    assert rate is None
    assert max_usd == 25.0
    assert bank_usd == 100.0


def test_config_from_args_cad_conversion_by_default():
    args = argparse.Namespace(
        sport="basketball_nba",
        legs=3,
        token_book="fanduel",
        boost=0.30,
        max_stake=28.4,
        bankroll=None,
        dry_run=False,
        no_fetch=False,
        recheck=False,
        save=False,
        market=None,
        min_gap_minutes=30,
        cache_ttl=300,
        api_key="",
        cad=True,
        fx_rate=1.42,
    )
    config = config_from_args(args)
    assert config.display_cad_rate == 1.42
    assert config.max_stake == pytest.approx(math.floor(28.4 / 1.42 * 100) / 100)


def test_parser_defaults_to_cad_stakes():
    from bonusarb.cli import build_parser

    args = build_parser().parse_args([])
    assert args.cad is True
    assert args.fx_rate is None


def test_parser_usd_flag_disables_cad():
    from bonusarb.cli import build_parser

    args = build_parser().parse_args(["--usd"])
    assert args.cad is False


def test_plan_panel_footer_includes_cad_when_rate_set():
    from datetime import datetime, timezone

    from bonusarb.models import HedgePlan, HedgeStep, Leg, TokenConstraint, TokenType
    from bonusarb.report import _plan_panel

    token = TokenConstraint(
        token_type=TokenType.PROFIT_BOOST,
        token_book="fanduel",
        min_legs=1,
        max_stake=20.0,
        boost_pct=0.0,
    )
    leg = Leg(
        game_id="g1",
        event_label="A @ B",
        commence_time=datetime.now(timezone.utc),
        selection="A",
        opposite_selection="B",
        token_book="fanduel",
        token_odds=2.0,
        hedge_book="polymarket",
        hedge_odds=1.9,
        hedge_token_id="t1",
    )
    step = HedgeStep(
        leg_index=1,
        event_label="A @ B",
        commence_time=leg.commence_time,
        selection="B",
        book="polymarket",
        odds=1.9,
        stake=15.0,
        place_by=leg.commence_time,
        hedge_token_id="t1",
    )
    plan = HedgePlan(
        legs=(leg,),
        token=token,
        stake=20.0,
        combined_odds=2.0,
        effective_win_profit=20.0,
        stake_cost=20.0,
        hedge_steps=(step,),
        locked_profit=2.0,
        max_cash_needed=35.0,
        roi=2.0 / 35.0,
        is_guaranteed=True,
    )
    panel = _plan_panel(plan, title="Test", cad_rate=1.42)
    # Panel renderable contains footer text via Group; stringify for assertion.
    from rich.console import Console
    from io import StringIO

    buf = StringIO()
    Console(file=buf, width=120).print(panel)
    text = buf.getvalue()
    assert "$28.40 CAD" in text  # sportsbook stake
    assert "$15.00 USD" in text  # Polymarket hedge
    assert "$2.84 CAD" in text  # locked profit
    assert "Polymarket" in text
    assert "EDT" in text or "EST" in text
