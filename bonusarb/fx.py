"""Live CAD/USD FX helpers for sportsbook (CAD) vs Polymarket (USDC) sizing.

Rates are fetched once per run from a public endpoint (no API key). The solver
stays in USD; conversion happens at input (CAD stake -> USD for sizing) and
display (USD profit -> CAD equivalent for reporting).
"""

from __future__ import annotations

import math

import requests

from bonusarb.config import AUTO_FX_SOURCE

DEFAULT_FX_SOURCE = "https://open.er-api.com/v6/latest/USD"


class FxError(RuntimeError):
    """Raised when a live FX rate cannot be fetched or parsed."""


def fetch_usd_cad_rate(
    source: str = AUTO_FX_SOURCE,
    *,
    timeout: float = 10.0,
) -> float:
    """Return how many CAD equal 1 USD (e.g. ~1.42).

    Uses the public ``open.er-api.com`` endpoint by default (hourly market
    rates, no key required).
    """
    url = source or DEFAULT_FX_SOURCE
    try:
        response = requests.get(url, timeout=timeout)
    except requests.RequestException as exc:
        raise FxError(f"FX request failed: {exc}") from exc
    if response.status_code != 200:
        raise FxError(f"FX API error {response.status_code}: {response.text[:200]}")
    try:
        payload = response.json()
        rate = float(payload["rates"]["CAD"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FxError(f"FX response missing CAD rate: {response.text[:200]}") from exc
    if rate <= 0:
        raise FxError(f"Invalid CAD rate: {rate}")
    return rate


def cad_to_usd(cad: float, usd_cad_rate: float) -> float:
    """Convert a CAD amount to USD using ``1 USD = usd_cad_rate CAD``.

    Floors to the nearest cent so the converted USD stake never exceeds the
    CAD budget (and so optimizer stake rounding cannot push past ``max_stake``).
    """
    if usd_cad_rate <= 0:
        raise ValueError(f"usd_cad_rate must be positive, got {usd_cad_rate}")
    return math.floor(cad / usd_cad_rate * 100) / 100


def usd_to_cad(usd: float, usd_cad_rate: float) -> float:
    """Convert a USD amount to CAD using ``1 USD = usd_cad_rate CAD``."""
    if usd_cad_rate <= 0:
        raise ValueError(f"usd_cad_rate must be positive, got {usd_cad_rate}")
    return usd * usd_cad_rate


def format_usd_cad(usd: float, usd_cad_rate: float | None, *, prefix: str = "$") -> str:
    """Format ``$X.XX USD`` with an optional ``/ $Y.YY CAD`` suffix."""
    text = f"{prefix}{usd:.2f} USD"
    if usd_cad_rate is not None and usd_cad_rate > 0:
        text += f" / ${usd_to_cad(usd, usd_cad_rate):.2f} CAD"
    return text


def format_sportsbook_amount(
    usd: float,
    usd_cad_rate: float | None,
    *,
    prefix: str = "$",
) -> str:
    """Sportsbook-facing money: CAD by default (when FX rate is set), else USD."""
    if usd_cad_rate is not None and usd_cad_rate > 0:
        return f"{prefix}{usd_to_cad(usd, usd_cad_rate):.2f} CAD"
    return f"{prefix}{usd:.2f} USD"


def format_usd_amount(usd: float, *, prefix: str = "$") -> str:
    """Polymarket / USDC amounts are always shown in USD."""
    return f"{prefix}{usd:.2f} USD"


def format_hedge_stake(
    usd: float,
    book: str,
    usd_cad_rate: float | None,
    *,
    prefix: str = "$",
) -> str:
    """Hedge stake display: Polymarket in USD; sportsbook hedges in CAD when applicable."""
    if book == "polymarket":
        return format_usd_amount(usd, prefix=prefix)
    return format_sportsbook_amount(usd, usd_cad_rate, prefix=prefix)
