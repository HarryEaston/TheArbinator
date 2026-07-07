"""Application configuration and constants."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from bonusarb.models import BookmakerKey

load_dotenv()

# Project root is one level up from this package.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / ".cache"
HISTORY_DIR = PROJECT_ROOT / "history"
SAMPLE_DATA_PATH = Path(__file__).resolve().parent / "data" / "sample_odds.json"
QUOTA_STATE_PATH = CACHE_DIR / "quota_state.json"

ODDS_API_BASE_URL = "https://api.the-odds-api.com/v4"
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")

DEFAULT_REGION = "us"
DEFAULT_BOOKMAKERS = ("fanduel", "draftkings")
DEFAULT_MARKET = "h2h"
DEFAULT_ODDS_FORMAT = "decimal"
DEFAULT_CACHE_TTL_SECONDS = 300
DEFAULT_CREDIT_WARN_THRESHOLD = 10
DEFAULT_MIN_GAP_MINUTES = 30
DEFAULT_BANKROLL = 1000.0

# The four leagues this tool supports, as (Odds API sport key, display name).
# Order controls the interactive picker order.
LEAGUES: tuple[tuple[str, str], ...] = (
    ("icehockey_nhl", "NHL"),
    ("americanfootball_nfl", "NFL"),
    ("baseball_mlb", "MLB"),
    ("basketball_nba", "NBA"),
)

# Estimated game duration in minutes by sport key prefix. Only the supported
# leagues are listed; the default fallback covers anything unexpected.
SPORT_DURATION_MINUTES: dict[str, int] = {
    "basketball": 150,
    "americanfootball": 210,
    "baseball": 180,
    "icehockey": 150,
}

DEFAULT_GAME_DURATION_MINUTES = 150


def available_hedge_books(token_book: BookmakerKey) -> tuple[BookmakerKey, ...]:
    """Return all hedge venues to search, excluding the token sportsbook.

    Polymarket is always included as a hedge venue. The optimizer picks the
    best opposite-side odds across this set.
    """
    books: list[BookmakerKey] = [book for book in DEFAULT_BOOKMAKERS if book != token_book]
    books.append("polymarket")
    return tuple(books)
