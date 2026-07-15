"""Configuration for the auto-hedger, sourced from environment variables."""

from __future__ import annotations

from dataclasses import dataclass

from bonusarb.config import (
    AUTO_LOST_THRESHOLD,
    AUTO_MAX_SLIPPAGE,
    AUTO_MIN_LOCKED_PROFIT_FLOOR,
    AUTO_MIN_ORDER_SHARES,
    AUTO_ORDER_FILL_SECONDS,
    AUTO_POLL_INTERVAL_SECONDS,
    AUTO_WON_THRESHOLD,
    POLYMARKET_CHAIN_ID,
    POLYMARKET_CLOB_HOST,
    POLYMARKET_FUNDER_ADDRESS,
    POLYMARKET_PRIVATE_KEY,
    POLYMARKET_SIGNATURE_TYPE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)


@dataclass
class AutoConfig:
    """All knobs the auto-hedger reads at runtime."""

    # Polymarket CLOB credentials.
    private_key: str
    funder_address: str
    signature_type: int
    chain_id: int
    clob_host: str

    # Telegram notifications.
    telegram_bot_token: str
    telegram_chat_id: str

    # Safety / sizing knobs.
    min_locked_profit_floor: float
    max_slippage: float
    poll_interval_seconds: float
    won_threshold: float
    lost_threshold: float
    min_order_shares: float
    order_fill_seconds: int

    @property
    def live_enabled(self) -> bool:
        """True when wallet credentials are present (live orders possible)."""
        return bool(self.private_key) and bool(self.funder_address)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token) and bool(self.telegram_chat_id)

    @classmethod
    def from_env(cls) -> "AutoConfig":
        return cls(
            private_key=POLYMARKET_PRIVATE_KEY,
            funder_address=POLYMARKET_FUNDER_ADDRESS,
            signature_type=POLYMARKET_SIGNATURE_TYPE,
            chain_id=POLYMARKET_CHAIN_ID,
            clob_host=POLYMARKET_CLOB_HOST,
            telegram_bot_token=TELEGRAM_BOT_TOKEN,
            telegram_chat_id=TELEGRAM_CHAT_ID,
            min_locked_profit_floor=AUTO_MIN_LOCKED_PROFIT_FLOOR,
            max_slippage=AUTO_MAX_SLIPPAGE,
            poll_interval_seconds=AUTO_POLL_INTERVAL_SECONDS,
            won_threshold=AUTO_WON_THRESHOLD,
            lost_threshold=AUTO_LOST_THRESHOLD,
            min_order_shares=AUTO_MIN_ORDER_SHARES,
            order_fill_seconds=AUTO_ORDER_FILL_SECONDS,
        )
