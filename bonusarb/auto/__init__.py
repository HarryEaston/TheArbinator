"""Auto Polymarket hedging.

Hands off from the scan: after you pick a parlay and place it on the
sportsbook, this package places the sequential Polymarket hedges via the CLOB,
live-tracks each leg's resolution, re-sizes for line movement, and notifies
over Telegram. Run with ``python -m bonusarb auto``.
"""

from bonusarb.auto.cli import auto_main

__all__ = ["auto_main"]
