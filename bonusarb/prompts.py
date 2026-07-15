"""Interactive prompt helpers for the CLI.

Each ``_prompt_*`` function renders a labelled step and reads ``input()`` with
validation and a default value. No Rich dependency here; output is plain text.
"""

from __future__ import annotations

from bonusarb.config import LEAGUES
from bonusarb.models import BookmakerKey, TOKEN_BOOKS

BOOK_LABELS: dict[str, str] = {
    "fanduel": "FanDuel",
    "draftkings": "DraftKings",
    "betmgm": "BetMGM",
    "espnbet": "theScore Bet",
    "polymarket": "Polymarket",
}


def print_step(step: int, total: int, title: str) -> None:
    if total > 0:
        print(f"--- Step {step}/{total}: {title} ---")
    else:
        print(f"--- Step {step}: {title} ---")


def prompt_league(step: int, total: int, *, default: str | None) -> str:
    print_step(step, total, "League")
    print()
    options = [(key, name) for key, name in LEAGUES]
    return prompt_numbered_choice(
        step,
        total,
        "Which league?",
        options,
        default=default or options[0][0],
        show_step_header=False,
    )


def prompt_data_source(
    step: int,
    total: int,
    dry_run_flag: bool,
    no_fetch_flag: bool,
) -> tuple[bool, bool]:
    print_step(step, total, "Data source")
    print()
    choice = prompt_numbered_choice(
        step,
        total,
        "Where should odds come from?",
        [
            ("live", "Live Odds API (uses API credits)"),
            ("dry_run", "Sample data / dry-run (no API credits)"),
            ("cache", "Cached data only (no new API call)"),
        ],
        default="live",
        show_step_header=False,
    )
    if choice == "live":
        return False, False
    if choice == "dry_run":
        return True, False
    return False, True


def prompt_book_step(
    step: int,
    total: int,
    question: str,
    *,
    default: str | None,
    books: tuple[BookmakerKey, ...] = TOKEN_BOOKS,
) -> BookmakerKey:
    print_step(step, total, question)
    print()
    options = [(book, BOOK_LABELS[book]) for book in books]
    value = prompt_numbered_choice(
        step,
        total,
        question,
        options,
        default=default or options[0][0],
        show_step_header=False,
    )
    return parse_token_book(value)


def prompt_float_step(
    step: int,
    total: int,
    question: str,
    *,
    minimum: float,
    default: float | None = None,
) -> float:
    print_step(step, total, question)
    print()
    default_text = f"{default:.2f}" if default is not None else None
    while True:
        raw = input(f"{question} [{default_text}]: " if default_text else f"{question}: ").strip()
        if not raw and default is not None:
            return default
        try:
            number = float(raw)
        except ValueError:
            print("Enter a valid number.")
            continue
        if number >= minimum:
            return number
        print(f"Enter a number >= {minimum}.")


def prompt_percent_step(
    step: int,
    total: int,
    question: str,
    *,
    default: float,
) -> float:
    print_step(step, total, question)
    print()
    default_pct = int(default * 100)
    while True:
        raw = input(f"{question} [{default_pct}%]: ").strip()
        if not raw:
            return default
        if raw.endswith("%"):
            raw = raw[:-1].strip()
        try:
            number = float(raw)
        except ValueError:
            print("Enter a number or percentage, e.g. 30 or 30%.")
            continue
        if number > 1.0:
            number /= 100.0
        if 0 <= number <= 5:
            return number
        print("Enter a boost between 0% and 500% (0 = no boost, standard arbitrage).")


def prompt_yes_no_step(step: int, total: int, question: str, *, default: bool) -> bool:
    print_step(step, total, question)
    print()
    default_label = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{question} [{default_label}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Enter y or n.")


def prompt_int_step(
    step: int,
    total: int,
    question: str,
    *,
    minimum: int,
    maximum: int,
    default: int,
) -> int:
    print_step(step, total, question)
    print()
    while True:
        raw = input(f"{question} [{default}]: ").strip()
        if not raw:
            return default
        if raw.lstrip("-").isdigit():
            value = int(raw)
            if minimum <= value <= maximum:
                return value
        print(f"Enter an integer between {minimum} and {maximum}.")


def prompt_numbered_choice(
    step: int,
    total: int,
    question: str,
    options: list[tuple[str, str]],
    *,
    default: str | None,
    show_step_header: bool = True,
) -> str:
    if show_step_header:
        print_step(step, total, question)
        print()

    default_index = None
    for index, (value, label) in enumerate(options, start=1):
        marker = " (default)" if value == default else ""
        print(f"  {index}. {label}{marker}")
        if value == default:
            default_index = index

    while True:
        hint = f" [{default_index}]" if default_index is not None else ""
        raw = input(f"Choice{hint}: ").strip()
        if not raw and default is not None:
            return default
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(options):
                return options[choice - 1][0]
        lowered = raw.lower()
        for value, _ in options:
            if lowered == value.lower():
                return value
        print(f"Enter a number from 1 to {len(options)}.")


def parse_token_book(value: str) -> BookmakerKey:
    aliases = {
        "thescore": "espnbet",
        "thescorebet": "espnbet",
        "the_score": "espnbet",
        "score": "espnbet",
    }
    normalized = aliases.get(value.lower().replace(" ", ""), value)
    if normalized not in TOKEN_BOOKS:
        raise ValueError(f"Unsupported token book: {value}")
    return normalized  # type: ignore[return-value]
