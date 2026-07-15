"""Telegram Bot API notifications for the auto-hedger.

A thin wrapper over the Bot API ``sendMessage`` endpoint. Notifications are
best-effort: a failed send logs to stderr and never raises, so a Telegram
outage can never block a hedge placement or stall the monitor loop.
"""

from __future__ import annotations

import sys

import requests

from bonusarb.fx import format_usd_amount

TELEGRAM_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = bool(bot_token) and bool(chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        url = f"{TELEGRAM_API_BASE}/bot{self.bot_token}/sendMessage"
        try:
            response = requests.post(
                url,
                json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
                timeout=15,
            )
        except requests.RequestException as exc:
            print(f"[notify] Telegram send failed: {exc}", file=sys.stderr)
            return False
        if response.status_code != 200:
            print(
                f"[notify] Telegram error {response.status_code}: {response.text}",
                file=sys.stderr,
            )
            return False
        return True

    # Convenience helpers so call sites read clearly.
    def hedge_placed(
        self,
        label: str,
        shares: float,
        price: float,
        cost: float,
    ) -> None:
        cost_str = format_usd_amount(cost)
        self.send(
            f"Hedge placed: {label}\n"
            f"  shares={shares:.2f} @ {price:.4f}\n"
            f"  cost={cost_str}"
        )

    def leg_resolved(self, label: str, won: bool) -> None:
        outcome = "WON (parlay alive)" if won else "LOST (parlay dead)"
        self.send(f"Leg resolved: {label} -> {outcome}")

    def complete(self, summary: str) -> None:
        self.send(f"Parlay complete.\n{summary}")

    def alert(self, message: str) -> None:
        self.send(f"ALERT (auto-hedger paused):\n{message}")
