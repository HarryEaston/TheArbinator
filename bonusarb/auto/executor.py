"""Polymarket CLOB order executor for the auto-hedger.

Re-prices the hedge at the live CLOB price, re-sizes the remaining stake so the
locked profit holds (reusing the sequential solver's closed-form recursion),
converts USD to shares, and submits a marketable limit order. Paper mode
simulates a full fill at the live price so the whole pipeline can be exercised
without a wallet or the ``py-clob-client`` SDK installed.

The real CLOB interaction is isolated behind ``ClobOrderApi`` (built lazily from
``py-clob-client``) so tests inject a fake. Live mode requires wallet
credentials; without them the executor refuses to arm.
"""

from __future__ import annotations

import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from bonusarb.arb.sequential import recompute_remaining_hedges
from bonusarb.auto.config import AutoConfig
from bonusarb.auto.state import LegState
from bonusarb.polymarket.client import (
    PolymarketClient,
    effective_taker_share_price,
    share_price_to_decimal_odds,
)


FILLED = "filled"
PARTIAL = "partial"
FAILED = "failed"
SKIPPED = "skipped"


@dataclass
class OrderResponse:
    order_id: str
    status: str  # "live" | "matched" | "cancelled" | "failed"


@dataclass
class OrderStatus:
    status: str  # "live" | "matched" | "cancelled"
    filled_shares: float
    avg_price: float


class OrderApi(Protocol):
    def post_marketable_order(
        self, token_id: str, shares: float, limit_price: float
    ) -> OrderResponse: ...

    def get_order_status(self, order_id: str) -> OrderStatus: ...

    def cancel_order(self, order_id: str) -> None: ...


@dataclass
class FillResult:
    status: str
    order_id: str | None
    filled_shares: float
    filled_price: float
    filled_cost: float
    live_price: float
    stake_usd: float
    locked_profit_after: float
    reason: str | None = None


@dataclass
class HedgeContext:
    """Everything needed to re-size a hedge given what is already sunk."""

    stake_cost: float
    win_profit: float
    already_placed_stakes: list[float]
    # Planned odds for the remaining legs (current leg first). The current leg's
    # entry is replaced by the live odds before solving; future legs keep their
    # planned odds and are re-priced when their turn arrives.
    remaining_planned_odds: list[float]


class ClobOrderApi:
    """Adapter over ``py-clob-client`` that posts marketable limit orders.

    Built lazily so importing this module never requires the SDK; only live
    execution does.
    """

    def __init__(self, config: AutoConfig) -> None:
        self.config = config
        self._client: Any = None

    def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from py_clob_client.client import ClobClient  # type: ignore
            from py_clob_client.clob_types import OrderArgs, OrderType  # type: ignore
            from py_clob_client.order_builder.constants import BUY  # type: ignore
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "Failed to import py-clob-client (is it installed?). "
                "Run `pip install -r requirements.txt` or use --paper mode. "
                f"Original error: {exc}"
            ) from exc

        self._client = ClobClient(
            host=self.config.clob_host,
            key=self.config.private_key,
            chain_id=self.config.chain_id,
            signature_type=self.config.signature_type,
            funder=self.config.funder_address,
        )
        creds = self._client.create_or_derive_api_creds()
        self._client.set_api_creds(creds)
        # Stash the classes for later use.
        self._OrderArgs = OrderArgs
        self._OrderType = OrderType
        self._BUY = BUY
        return self._client

    def check_wallet(self) -> dict:
        """Empirically verify wallet connectivity + signature/funder setup.

        Returns a dict with the derived signer address and USDC balance so you
        can confirm the proxy/Safe address and signature_type match your
        Polymarket deposit before trusting it with real orders.
        """
        client = self._ensure_client()
        info: dict = {"signer": "", "balance_allowance": None}
        try:
            info["signer"] = client.get_address() or ""
        except Exception as exc:  # pragma: no cover - depends on env
            info["signer_error"] = str(exc)
        try:
            from py_clob_client.clob_types import (  # type: ignore
                AssetType,
                BalanceAllowanceParams,
            )

            info["balance_allowance"] = client.get_balance_allowance(
                BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            )
        except Exception as exc:  # pragma: no cover - depends on env
            info["balance_error"] = str(exc)
        return info

    def post_marketable_order(
        self, token_id: str, shares: float, limit_price: float
    ) -> OrderResponse:
        client = self._ensure_client()
        order_args = self._OrderArgs(
            token_id=token_id,
            price=round(limit_price, 6),
            size=round(shares, 2),
            side=self._BUY,
        )
        signed = client.create_order(order_args)
        # FAK (fill-and-kill / IOC) takes what is available at the limit price
        # and cancels the rest, so we never chase the book with a live GTC.
        resp = client.post_order(signed, order_type=self._OrderType.FAK)
        order_id = (
            resp.get("orderID") or resp.get("order_id") or resp.get("id") or ""
            if isinstance(resp, dict)
            else getattr(resp, "order_id", getattr(resp, "orderID", ""))
        )
        status = (
            resp.get("status", "live") if isinstance(resp, dict) else getattr(resp, "status", "live")
        )
        return OrderResponse(order_id=str(order_id), status=str(status))

    def get_order_status(self, order_id: str) -> OrderStatus:
        client = self._ensure_client()
        try:
            order = client.get_order(order_id)
        except Exception as exc:  # pragma: no cover - depends on env
            print(f"[executor] get_order failed: {exc}", file=sys.stderr)
            return OrderStatus(status="live", filled_shares=0.0, avg_price=0.0)
        if isinstance(order, dict):
            status = str(order.get("status", "live"))
            size = float(order.get("size_matched", order.get("filled_size", 0.0)) or 0.0)
            price = float(order.get("price", order.get("avg_price", 0.0)) or 0.0)
            return OrderStatus(status=status, filled_shares=size, avg_price=price)
        size = float(getattr(order, "size_matched", 0.0) or 0.0)
        price = float(getattr(order, "price", 0.0) or 0.0)
        return OrderStatus(status=str(getattr(order, "status", "live")), filled_shares=size, avg_price=price)

    def cancel_order(self, order_id: str) -> None:
        client = self._ensure_client()
        try:
            client.cancel(order_id)
        except Exception as exc:  # pragma: no cover - depends on env
            print(f"[executor] cancel failed: {exc}", file=sys.stderr)


class ClobExecutor:
    def __init__(
        self,
        config: AutoConfig,
        polymarket_client: PolymarketClient,
        *,
        paper: bool = True,
        order_api: OrderApi | None = None,
    ) -> None:
        self.config = config
        self.polymarket_client = polymarket_client
        self.paper = paper
        self._order_api = order_api

    @property
    def order_api(self) -> OrderApi:
        if self._order_api is None:
            if self.paper:
                self._order_api = _PaperOrderApi()
            else:
                if not self.config.live_enabled:
                    raise RuntimeError(
                        "Live mode requires POLYMARKET_PRIVATE_KEY and "
                        "POLYMARKET_FUNDER_ADDRESS in .env."
                    )
                self._order_api = ClobOrderApi(self.config)
        return self._order_api

    def place_hedge(self, leg: LegState, context: HedgeContext) -> FillResult:
        if not leg.hedge_token_id:
            return FillResult(
                FAILED, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                reason="No Polymarket token id on this leg (non-Polymarket hedge).",
            )

        live_price = self.polymarket_client.get_token_price(
            leg.hedge_token_id, side="BUY", force_fetch=True
        )
        if live_price is None or not 0.0 < live_price < 1.0:
            return FillResult(
                FAILED, None, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                reason=f"No live price for token {leg.hedge_token_id}.",
            )

        live_odds = share_price_to_decimal_odds(live_price)
        effective_price = effective_taker_share_price(live_price)
        remaining_odds = list(context.remaining_planned_odds)
        if not remaining_odds:
            return FillResult(
                FAILED, None, 0.0, 0.0, 0.0, live_price, 0.0, 0.0,
                reason="No remaining legs to hedge.",
            )
        # Replace the current (first) leg's planned odds with the live quote.
        remaining_odds[0] = live_odds

        locked_profit, stakes = recompute_remaining_hedges(
            stake_cost=context.stake_cost,
            win_profit=context.win_profit,
            remaining_hedge_odds=remaining_odds,
            already_placed_stakes=context.already_placed_stakes,
        )
        stake_usd = stakes[0]

        if locked_profit < self.config.min_locked_profit_floor:
            return FillResult(
                SKIPPED, None, 0.0, 0.0, 0.0, live_price, stake_usd, locked_profit,
                reason=(
                    f"Locked profit ${locked_profit:.2f} below floor "
                    f"${self.config.min_locked_profit_floor:.2f} at live price {live_price:.4f}."
                ),
            )

        shares = stake_usd / effective_price
        if shares < self.config.min_order_shares:
            return FillResult(
                SKIPPED, None, 0.0, 0.0, 0.0, live_price, stake_usd, locked_profit,
                reason=(
                    f"Order size {shares:.2f} shares below minimum "
                    f"{self.config.min_order_shares:.2f}."
                ),
            )

        limit_price = min(live_price * (1.0 + self.config.max_slippage), 0.999)

        if self.paper:
            return FillResult(
                FILLED,
                order_id=f"paper-{uuid.uuid4().hex[:8]}",
                filled_shares=shares,
                filled_price=live_price,
                filled_cost=shares * live_price,
                live_price=live_price,
                stake_usd=stake_usd,
                locked_profit_after=locked_profit,
                reason="paper fill",
            )

        response = self.order_api.post_marketable_order(leg.hedge_token_id, shares, limit_price)
        if not response.order_id:
            return FillResult(
                FAILED, None, 0.0, 0.0, 0.0, live_price, stake_usd, locked_profit,
                reason="CLOB rejected the order (no order id returned).",
            )

        status = self._poll_fill(response.order_id)
        self.order_api.cancel_order(response.order_id)
        if status.filled_shares <= 0.0:
            return FillResult(
                FAILED, response.order_id, 0.0, 0.0, 0.0, live_price, stake_usd, locked_profit,
                reason="Order did not fill.",
            )
        fill_status = FILLED if status.filled_shares >= shares - 1e-6 else PARTIAL
        return FillResult(
            fill_status,
            response.order_id,
            status.filled_shares,
            status.avg_price or live_price,
            status.filled_shares * (status.avg_price or live_price),
            live_price,
            stake_usd,
            locked_profit,
            reason="partial fill" if fill_status == PARTIAL else None,
        )

    def _poll_fill(self, order_id: str) -> OrderStatus:
        deadline = time.time() + max(self.config.order_fill_seconds, 1)
        last = OrderStatus(status="live", filled_shares=0.0, avg_price=0.0)
        while time.time() < deadline:
            last = self.order_api.get_order_status(order_id)
            if last.status in {"matched", "cancelled", "filled"}:
                return last
            time.sleep(1.0)
        return last


class _PaperOrderApi:
    """Default order api for paper mode; never used for real placement."""

    def post_marketable_order(
        self, token_id: str, shares: float, limit_price: float
    ) -> OrderResponse:
        return OrderResponse(order_id=f"paper-{uuid.uuid4().hex[:8]}", status="matched")

    def get_order_status(self, order_id: str) -> OrderStatus:
        return OrderStatus(status="matched", filled_shares=0.0, avg_price=0.0)

    def cancel_order(self, order_id: str) -> None:
        return None
