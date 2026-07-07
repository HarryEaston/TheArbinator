"""Tests for interactive CLI helpers."""

import argparse

from bonusarb.cli import _should_use_interactive
from bonusarb.config import available_hedge_books
from bonusarb.odds_utils import parse_market_keys
from bonusarb.prompts import prompt_numbered_choice, prompt_percent_step


def test_should_use_interactive_by_default():
    args = argparse.Namespace(batch=False, interactive=False)
    assert _should_use_interactive(args) is True


def test_should_use_batch_mode():
    args = argparse.Namespace(batch=True, interactive=False)
    assert _should_use_interactive(args) is False


def test_parse_market_keys_all_guaranteed():
    assert parse_market_keys("h2h,spreads,totals") == ("h2h", "spreads", "totals")


def test_numbered_choice_accepts_default(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "")
    value = prompt_numbered_choice(
        1,
        0,
        "Pick one",
        [("a", "Option A"), ("b", "Option B")],
        default="b",
    )
    assert value == "b"


def test_prompt_percent_step_accepts_zero_boost(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "0")
    value = prompt_percent_step(
        1,
        0,
        "What is the profit boost percentage?",
        default=0.30,
    )
    assert value == 0.0


def test_available_hedge_books_excludes_token_book_and_adds_polymarket():
    assert available_hedge_books("fanduel") == ("draftkings", "polymarket")
    assert available_hedge_books("draftkings") == ("fanduel", "polymarket")
