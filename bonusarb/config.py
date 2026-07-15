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

# --- Auto-hedger (Polymarket CLOB order placement) -------------------------
# Live order placement requires a funded Polymarket wallet on Polygon. For a
# browser-deposited (MetaMask) wallet, the proxy/Safe address is the funder and
# signature_type defaults to 2 (POLY_GNOSIS_SAFE). Verify empirically before
# trusting it with real funds.
STATE_DIR = PROJECT_ROOT / "state"

POLYMARKET_CLOB_HOST = os.getenv("POLYMARKET_CLOB_HOST", "https://clob.polymarket.com")
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_FUNDER_ADDRESS = os.getenv("POLYMARKET_FUNDER_ADDRESS", "")
POLYMARKET_SIGNATURE_TYPE = int(os.getenv("POLYMARKET_SIGNATURE_TYPE", "2"))
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
# Sports taker fee rate for hedge odds (fee = shares × rate × p × (1 − p)).
POLYMARKET_SPORTS_TAKER_FEE_RATE = float(
    os.getenv("POLYMARKET_SPORTS_TAKER_FEE_RATE", "0.05")
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

AUTO_MIN_LOCKED_PROFIT_FLOOR = float(os.getenv("AUTO_MIN_LOCKED_PROFIT_FLOOR", "1.0"))
AUTO_MAX_SLIPPAGE = float(os.getenv("AUTO_MAX_SLIPPAGE", "0.02"))
AUTO_POLL_INTERVAL_SECONDS = float(os.getenv("AUTO_POLL_INTERVAL_SECONDS", "60"))
# Backward-compat alias: if AUTO_LOST_THRESHOLD is unset, fall back to this.
AUTO_DECIDED_THRESHOLD = float(os.getenv("AUTO_DECIDED_THRESHOLD", "0.97"))
AUTO_WON_THRESHOLD = float(os.getenv("AUTO_WON_THRESHOLD", "0.99"))
AUTO_LOST_THRESHOLD = float(
    os.getenv("AUTO_LOST_THRESHOLD", os.getenv("AUTO_DECIDED_THRESHOLD", "0.97"))
)
AUTO_MIN_ORDER_SHARES = float(os.getenv("AUTO_MIN_ORDER_SHARES", "5"))
AUTO_ORDER_FILL_SECONDS = int(os.getenv("AUTO_ORDER_FILL_SECONDS", "30"))

# Live CAD/USD rate source for --cad stake conversion (no API key).
AUTO_FX_SOURCE = os.getenv("AUTO_FX_SOURCE", "https://open.er-api.com/v6/latest/USD")

# The Odds API has no Ontario/Canada region. FanDuel, DraftKings, and BetMGM
# are under ``us``; theScore Bet (``espnbet``, formerly ESPN Bet) is under
# ``us2``. Requesting books by key takes priority over region, so we keep
# ``regions=us`` and list all four books explicitly (still 1 region of cost).
DEFAULT_REGION = "us"
DEFAULT_BOOKMAKERS: tuple[BookmakerKey, ...] = (
    "fanduel",
    "draftkings",
    "betmgm",
    "espnbet",
)
DEFAULT_MARKET = "h2h"
DEFAULT_ODDS_FORMAT = "decimal"
DEFAULT_CACHE_TTL_SECONDS = 1800
DEFAULT_CREDIT_WARN_THRESHOLD = 10
DEFAULT_MIN_GAP_MINUTES = 30
DEFAULT_BANKROLL = 1000.0

# The leagues this tool supports, as (Odds API sport key, display name).
# Order controls the interactive picker order.
LEAGUES: tuple[tuple[str, str], ...] = (
    ("icehockey_nhl", "NHL"),
    ("americanfootball_nfl", "NFL"),
    ("baseball_mlb", "MLB"),
    ("basketball_nba", "NBA"),
    ("basketball_wnba", "WNBA"),
    ("mma_mixed_martial_arts", "UFC"),
)

# Estimated game duration in minutes by sport key prefix. Only the supported
# leagues are listed; the default fallback covers anything unexpected.
# MMA fights typically end well inside 45 minutes (including decision/scoring);
# a short duration lets multiple fights on the same card form a parlay.
SPORT_DURATION_MINUTES: dict[str, int] = {
    "basketball": 150,
    "americanfootball": 210,
    "baseball": 180,
    "icehockey": 150,
    "mma": 45,
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
