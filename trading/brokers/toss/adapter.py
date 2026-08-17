"""Toss as a `BrokerPort`.

This is where Toss stops looking like Toss. Everything above expects the shapes
KIS established — `stock_code` not `symbol`, a percentage not a fraction, a
result dict rather than an exception — because those shapes are what the
tracking agent, the dashboards and `ExecutionService` already read. Translating
here is the whole reason the port exists; the alternative is editing every call
site, which is the cost this work was meant to remove.

Three translations are easy to get wrong and expensive to notice.

Toss reports `profitLoss.rate` as a fraction (0.1177) and KIS reports
`profit_rate` as a percentage (11.77). Passing it through unscaled is wrong by
a factor of a hundred and looks entirely plausible on a dashboard.

Toss returns every number as a decimal string. Handing those to callers that
do arithmetic gives string concatenation or a TypeError, depending on where it
lands.

Creating an order returns only ids — no fill. The fill has to be read back, and
an adapter that assumes acceptance means execution would report a resting order
as a completed buy.

Order methods never raise for a business outcome. `ExecutionService` classifies
orders by `success` and `outcome_unknown` on the returned dict, so an adapter
that raised on a rejected order would have it recorded as an unknown outcome
and the position left blocked.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import math
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from trading.brokers.base import BrokerUnavailable, BrokerUnsupported, HoldingState
from trading.brokers.toss import ratelimit
from trading.brokers.toss.errors import TossApiError

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

DEFAULT_BUY_AMOUNT_KRW = 100_000


def _now_kst_iso() -> str:
    return datetime.datetime.now(KST).isoformat()


def _num(value: Any, default: float = 0.0) -> float:
    """Toss decimal-string → float, tolerating null and junk."""
    if value is None:
        return default
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return default


class TossBroker:
    """Trade one market through Toss, speaking the shapes PRISM already reads."""

    name = "toss"

    def __init__(
        self,
        client: Any,
        *,
        market: str = "KR",
        buy_amount: int | None = None,
        currency: str | None = None,
    ):
        normalized = str(market).upper()
        if normalized not in {"KR", "US"}:
            raise ValueError(f"market must be KR or US, got {market!r}")
        self._client = client
        self.market = normalized
        self.currency = currency or ("KRW" if normalized == "KR" else "USD")
        self.buy_amount = buy_amount if buy_amount is not None else DEFAULT_BUY_AMOUNT_KRW

    @property
    def client(self) -> Any:
        return self._client

    # ── Orders ────────────────────────────────────────────────────────────────

    async def async_buy_stock(
        self,
        stock_code: str,
        buy_amount: int | None = None,
        timeout: float = 30.0,
        limit_price: int | None = None,
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._buy, stock_code, buy_amount, limit_price),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # The order may well have been accepted. Reporting plain failure
            # would let a caller re-enter a position it already holds.
            return self._outcome(
                stock_code,
                success=False,
                message=f"Buy request timeout ({timeout}s)",
                outcome_unknown=True,
            )

    async def async_sell_stock(
        self,
        stock_code: str,
        timeout: float = 30.0,
        limit_price: int | None = None,
        quantity: int | None = None,
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self._sell, stock_code, limit_price, quantity),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return self._outcome(
                stock_code,
                success=False,
                message=f"Sell request timeout ({timeout}s)",
                outcome_unknown=True,
            )

    def _buy(
        self, stock_code: str, buy_amount: int | None, limit_price: int | None
    ) -> dict[str, Any]:
        amount = buy_amount if buy_amount else self.buy_amount

        try:
            price_info = self.get_current_price(stock_code)
        except BrokerUnavailable as exc:
            return self._outcome(stock_code, success=False, message=f"Failed to get current price: {exc}")

        if not price_info or not price_info.get("current_price"):
            return self._outcome(stock_code, success=False, message="Failed to get current price")

        current_price = price_info["current_price"]
        quantity = math.floor(amount / current_price) if current_price else 0
        if quantity <= 0:
            return self._outcome(
                stock_code,
                success=False,
                current_price=current_price,
                message=f"Buyable quantity is 0 (buy amount: {amount:,})",
            )

        # KIS sends the current price as the limit when none is given, and the
        # tracking logic is tuned to that behaviour; diverging here would make
        # the two brokers fill differently from identical signals.
        effective_limit = int(limit_price) if limit_price and limit_price > 0 else int(current_price)

        return self._submit(
            stock_code,
            side="BUY",
            quantity=quantity,
            limit_price=effective_limit,
            reference_price=current_price,
        )

    def _sell(
        self, stock_code: str, limit_price: int | None, quantity: int | None
    ) -> dict[str, Any]:
        state, held = self.get_holding_quantity_checked(stock_code)
        if state == "UNKNOWN":
            return self._outcome(
                stock_code,
                success=False,
                message="Holding query failed; refusing to sell on an unverified position",
                outcome_unknown=True,
            )
        if state == "FLAT" or not held:
            return self._outcome(stock_code, success=False, message="No holding to sell")

        sell_quantity = min(int(quantity), held) if quantity else held
        if sell_quantity <= 0:
            return self._outcome(stock_code, success=False, message="Sell quantity resolved to 0")

        try:
            price_info = self.get_current_price(stock_code)
        except BrokerUnavailable as exc:
            return self._outcome(stock_code, success=False, message=f"Failed to get current price: {exc}")

        current_price = (price_info or {}).get("current_price") or 0
        effective_limit = int(limit_price) if limit_price and limit_price > 0 else int(current_price)
        if effective_limit <= 0:
            return self._outcome(stock_code, success=False, message="Failed to get current price")

        return self._submit(
            stock_code,
            side="SELL",
            quantity=sell_quantity,
            limit_price=effective_limit,
            reference_price=current_price,
        )

    def _submit(
        self,
        stock_code: str,
        *,
        side: str,
        quantity: int,
        limit_price: int,
        reference_price: float,
    ) -> dict[str, Any]:
        # A fresh key per attempt, so the client may safely retry a request
        # whose response was lost: Toss replays the original result for ten
        # minutes rather than creating a second order.
        client_order_id = f"prism-{uuid.uuid4().hex[:24]}"

        body = {
            "clientOrderId": client_order_id,
            "symbol": stock_code,
            "side": side,
            "orderType": "LIMIT",
            "quantity": str(quantity),
            "price": self._format_price(limit_price),
        }

        try:
            created = self._client.request(
                "POST",
                "/api/v1/orders",
                json_body=body,
                group=ratelimit.ORDER,
                needs_account=True,
                idempotent=True,
            )
        except TossApiError as exc:
            if exc.is_business_refusal:
                # Toss understood and declined. That is an answer, not a fault.
                logger.error("[TOSS] %s %s refused: %s", side, stock_code, exc)
                return self._outcome(
                    stock_code,
                    success=False,
                    current_price=reference_price,
                    quantity=quantity,
                    message=f"{side} rejected: {exc.code}: {exc.message}",
                )
            logger.error("[TOSS] %s %s failed ambiguously: %s", side, stock_code, exc)
            return self._outcome(
                stock_code,
                success=False,
                current_price=reference_price,
                quantity=quantity,
                message=f"{side} failed: {exc}",
                outcome_unknown=True,
            )
        except BrokerUnavailable as exc:
            return self._outcome(
                stock_code,
                success=False,
                current_price=reference_price,
                quantity=quantity,
                message=f"{side} failed: {exc}",
                outcome_unknown=True,
            )

        order_id = (created or {}).get("orderId")
        if not order_id:
            return self._outcome(
                stock_code,
                success=False,
                current_price=reference_price,
                quantity=quantity,
                message=f"{side} response carried no orderId",
                outcome_unknown=True,
            )

        detail = self._read_back(order_id)
        return self._outcome_from_order(
            stock_code,
            side=side,
            order_id=order_id,
            requested_quantity=quantity,
            reference_price=reference_price,
            detail=detail,
        )

    def _read_back(self, order_id: str) -> dict[str, Any] | None:
        """Creation returns ids only; the fill has to be fetched separately."""
        try:
            return self._client.request(
                "GET", f"/api/v1/orders/{order_id}", needs_account=True
            )
        except (TossApiError, BrokerUnavailable) as exc:
            # The order exists — we simply could not read its state. Say so
            # rather than guessing either way.
            logger.warning("[TOSS] could not read back order %s: %s", order_id, exc)
            return None

    def _outcome_from_order(
        self,
        stock_code: str,
        *,
        side: str,
        order_id: str,
        requested_quantity: int,
        reference_price: float,
        detail: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if detail is None:
            return self._outcome(
                stock_code,
                success=False,
                current_price=reference_price,
                quantity=requested_quantity,
                order_no=order_id,
                message=f"{side} submitted but its status could not be read",
                outcome_unknown=True,
            )

        status = str(detail.get("status") or "").upper()
        execution = detail.get("execution") or {}
        filled = _num(execution.get("filledQuantity"))
        avg_price = _num(execution.get("averageFilledPrice")) or reference_price

        if status in {"REJECTED", "CANCEL_REJECTED", "REPLACE_REJECTED"}:
            return self._outcome(
                stock_code,
                success=False,
                current_price=reference_price,
                quantity=requested_quantity,
                order_no=order_id,
                message=f"{side} rejected by Toss (status={status})",
            )

        # PENDING is a live resting order, not a failure — KIS reports an
        # accepted order the same way, and the tracking agent reconciles later.
        accepted = status in {"FILLED", "PARTIAL_FILLED", "PENDING", "PENDING_REPLACE"}
        quantity = int(filled) if filled else requested_quantity
        total = quantity * avg_price

        return self._outcome(
            stock_code,
            success=accepted,
            current_price=avg_price or reference_price,
            quantity=quantity,
            total_amount=total,
            order_no=order_id,
            message=(
                f"{side} completed: {quantity} x {avg_price:,.0f} = {total:,.0f}"
                if status == "FILLED"
                else f"{side} accepted (status={status})"
            ),
        )

    def _format_price(self, price: int | float) -> str:
        """KR prices are whole won; US allows decimals."""
        if self.market == "KR":
            return str(int(price))
        return f"{float(price):.4f}".rstrip("0").rstrip(".")

    def _outcome(
        self,
        stock_code: str,
        *,
        success: bool,
        current_price: float = 0,
        quantity: int = 0,
        total_amount: float = 0,
        order_no: str | None = None,
        message: str = "",
        outcome_unknown: bool = False,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "success": success,
            "stock_code": stock_code,
            "current_price": current_price,
            "quantity": quantity,
            "total_amount": total_amount,
            "order_no": order_no,
            "message": message,
            "timestamp": _now_kst_iso(),
        }
        if outcome_unknown:
            result["outcome_unknown"] = True
        return result

    # ── Amend / cancel ────────────────────────────────────────────────────────

    def amend_order(
        self,
        stock_code: str,
        orgn_odno: str,
        limit_price: int,
        quantity: int | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"price": self._format_price(limit_price)}
        if quantity:
            body["quantity"] = str(quantity)
        return self._mutate_order(stock_code, orgn_odno, "modify", body, "Amend")

    def cancel_order(
        self, stock_code: str, orgn_odno: str, quantity: int | None = None, **_: Any
    ) -> dict[str, Any]:
        return self._mutate_order(stock_code, orgn_odno, "cancel", {}, "Cancel")

    def _mutate_order(
        self, stock_code: str, order_id: str, action: str, body: dict[str, Any], label: str
    ) -> dict[str, Any]:
        try:
            self._client.request(
                "POST",
                f"/api/v1/orders/{order_id}/{action}",
                json_body=body,
                group=ratelimit.ORDER,
                needs_account=True,
            )
        except TossApiError as exc:
            return self._outcome(
                stock_code,
                success=False,
                order_no=order_id,
                message=f"{label} rejected: {exc.code}: {exc.message}",
                outcome_unknown=not exc.is_business_refusal,
            )
        except BrokerUnavailable as exc:
            return self._outcome(
                stock_code,
                success=False,
                order_no=order_id,
                message=f"{label} failed: {exc}",
                outcome_unknown=True,
            )
        return self._outcome(
            stock_code, success=True, order_no=order_id, message=f"{label} accepted"
        )

    # ── Reserved orders: Toss has none ────────────────────────────────────────

    def buy_reserved_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise BrokerUnsupported(
            "Toss has no time-based reserved order; only price-triggered "
            "conditional orders, which mean something different. See PRD Phase 6."
        )

    def sell_reserved_order(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise BrokerUnsupported(
            "Toss has no time-based reserved order; only price-triggered "
            "conditional orders, which mean something different. See PRD Phase 6."
        )

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_current_price(self, stock_code: str, *_: Any, **__: Any) -> dict[str, Any] | None:
        try:
            result = self._client.request(
                "GET", "/api/v1/prices", params={"symbols": stock_code}, group=ratelimit.DEFAULT
            )
        except (TossApiError, BrokerUnavailable) as exc:
            logger.warning("[TOSS] price lookup failed for %s: %s", stock_code, exc)
            return None

        rows = result if isinstance(result, list) else [result]
        for row in rows:
            if not isinstance(row, dict) or str(row.get("symbol")) != stock_code:
                continue
            price = _num(row.get("lastPrice"))
            if price <= 0:
                continue
            return {
                "stock_code": stock_code,
                "stock_name": str(row.get("name") or stock_code),
                # KIS reports KR prices as int and callers index arithmetic off
                # that; keeping the type identical avoids surprises downstream.
                "current_price": int(price) if self.market == "KR" else price,
                "change_rate": _num(row.get("changeRate")),
                "volume": _int(row.get("volume")),
            }
        return None

    def _holdings(self) -> dict[str, Any]:
        return self._client.request("GET", "/api/v1/holdings", needs_account=True) or {}

    def get_portfolio(self) -> list[dict[str, Any]]:
        try:
            authoritative, rows = self._portfolio_checked()
        except (TossApiError, BrokerUnavailable):
            return []
        return rows if authoritative else []

    def _portfolio_checked(self) -> tuple[bool, list[dict[str, Any]]]:
        """Rows plus whether Toss actually answered.

        Unlike the KIS US trader, Toss can distinguish these, so the port's
        three-state holding contract is honoured properly rather than degraded.
        """
        try:
            overview = self._holdings()
        except (TossApiError, BrokerUnavailable) as exc:
            logger.warning("[TOSS] holdings query failed: %s", exc)
            return False, []

        items = overview.get("items")
        if not isinstance(items, list):
            return False, []

        portfolio = []
        for item in items:
            if not isinstance(item, dict):
                return False, []
            symbol = str(item.get("symbol") or "")
            if not symbol:
                return False, []
            if self.market == "KR" and str(item.get("currency")) != "KRW":
                continue
            if self.market == "US" and str(item.get("currency")) == "KRW":
                continue

            quantity = _int(item.get("quantity"))
            if quantity <= 0:
                continue

            market_value = item.get("marketValue") or {}
            profit_loss = item.get("profitLoss") or {}
            portfolio.append(
                {
                    "stock_code": symbol,
                    "stock_name": str(item.get("name") or symbol),
                    "quantity": quantity,
                    "avg_price": _num(item.get("averagePurchasePrice")),
                    "current_price": _num(item.get("lastPrice")),
                    "eval_amount": _num(market_value.get("amount")),
                    "profit_amount": _num(profit_loss.get("amount")),
                    # Toss gives a fraction; KIS gives a percentage. Callers
                    # read the KIS convention.
                    "profit_rate": round(_num(profit_loss.get("rate")) * 100, 2),
                }
            )
        return True, portfolio

    def get_holding_quantity(self, stock_code: str, *_: Any, **__: Any) -> int:
        state, quantity = self.get_holding_quantity_checked(stock_code)
        return quantity if state == "HELD" and quantity else 0

    def get_holding_quantity_checked(self, stock_code: str, *_: Any, **__: Any) -> HoldingState:
        authoritative, portfolio = self._portfolio_checked()
        if not authoritative:
            return "UNKNOWN", None
        for holding in portfolio:
            if holding["stock_code"] == stock_code:
                return "HELD", int(holding["quantity"])
        return "FLAT", 0

    def get_account_summary(self) -> dict[str, Any]:
        try:
            overview = self._holdings()
        except (TossApiError, BrokerUnavailable) as exc:
            logger.error("[TOSS] account summary failed: %s", exc)
            return {}
        if not overview:
            return {}

        key = "krw" if self.currency == "KRW" else "usd"
        market_value = ((overview.get("marketValue") or {}).get("amount") or {}).get(key)
        purchase = (overview.get("totalPurchaseAmount") or {}).get(key)
        profit = ((overview.get("profitLoss") or {}).get("amount") or {}).get(key)

        try:
            cash = _num(
                (
                    self._client.request(
                        "GET",
                        "/api/v1/buying-power",
                        params={"currency": self.currency},
                        needs_account=True,
                    )
                    or {}
                ).get("cashBuyingPower")
            )
        except (TossApiError, BrokerUnavailable) as exc:
            logger.warning("[TOSS] buying power unavailable: %s", exc)
            cash = 0.0

        eval_amount = _num(market_value)
        purchase_amount = _num(purchase) or 1.0
        profit_amount = _num(profit)

        return {
            "total_eval_amount": eval_amount + cash,
            "total_profit_amount": profit_amount,
            "total_profit_rate": round(profit_amount / purchase_amount * 100, 2),
            "deposit": cash,
            "total_cash": cash,
            "available_amount": cash,
        }

    def calculate_buy_quantity(
        self, stock_code: str, buy_amount: int | None = None, *_: Any, **__: Any
    ) -> int:
        amount = buy_amount if buy_amount else self.buy_amount
        price_info = self.get_current_price(stock_code)
        if not price_info or not price_info.get("current_price"):
            return 0
        return math.floor(amount / price_info["current_price"])


def toss_domestic(client: Any, **kwargs: Any) -> TossBroker:
    """Wrap a Toss client for the Korean market."""
    return TossBroker(client, market="KR", **kwargs)
