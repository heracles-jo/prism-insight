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
from decimal import ROUND_DOWN, Decimal, InvalidOperation
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


def _dec(value: Any, default: str = "0") -> Decimal:
    """Toss decimal-string → `Decimal`, keeping every digit it sent.

    Share counts go through here rather than `_num`: a float round-trip of
    0.788569 loses exactness, and a sell-everything order computed from a lossy
    quantity leaves a sliver behind that never closes.
    """
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


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
        # Company names are static; look each up once per process.
        self._names: dict[str, str] = {}

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
            # The budget cannot reach one whole share. On US that is not a dead
            # end: Toss sells fractions by amount, which is how a $100 budget
            # buys a $185 stock at all. Everywhere else it stays a refusal.
            return self._buy_by_amount(stock_code, amount, current_price)

        # KIS sends the current price as the limit when none is given, and the
        # tracking logic is tuned to that behaviour; diverging here would make
        # the two brokers fill differently from identical signals.
        effective_limit = self._tick(limit_price if limit_price and limit_price > 0 else current_price)

        return self._submit(
            stock_code,
            side="BUY",
            quantity=quantity,
            limit_price=effective_limit,
            reference_price=current_price,
        )

    def _buy_by_amount(
        self, stock_code: str, amount: float, reference_price: float
    ) -> dict[str, Any]:
        """Buy a fraction of a share by spending a fixed amount.

        Toss confirms the quantity only after it fills — `orderAmount` fixes the
        money and lets the share count float — so the fill is read back rather
        than assumed, the same as every other order here.
        """
        if self.market != "US":
            return self._outcome(
                stock_code,
                success=False,
                current_price=reference_price,
                message=(
                    f"Buyable quantity is 0 (buy amount: {amount:,}). Toss accepts "
                    "amount-based orders on US stocks only; domestic orders must "
                    "reach one whole share."
                ),
            )
        if not self.fractional_window_open():
            return self._outcome(
                stock_code,
                success=False,
                current_price=reference_price,
                message=(
                    f"Buyable quantity is 0 (buy amount: {amount:,}) and an "
                    "amount-based order cannot be placed now: Toss accepts them "
                    "only from the regular session open until an hour before "
                    "its close."
                ),
            )

        order_amount = _dec(amount).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if order_amount <= 0:
            return self._outcome(
                stock_code,
                success=False,
                current_price=reference_price,
                message=f"Order amount resolved to 0 (buy amount: {amount:,})",
            )

        body = {
            "clientOrderId": f"prism-{uuid.uuid4().hex[:24]}",
            "symbol": stock_code,
            "side": "BUY",
            # Amount-based orders are market orders; a price is rejected.
            "orderType": "MARKET",
            "orderAmount": format(order_amount, "f"),
        }
        logger.info(
            "[TOSS] BUY %s by amount %s (a whole share costs %s)",
            stock_code, body["orderAmount"], reference_price,
        )

        return self._dispatch(
            body,
            stock_code=stock_code,
            side="BUY",
            # Unknown until it fills; the read-back supplies the real number.
            quantity=Decimal("0"),
            reference_price=reference_price,
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

        # `held` may be a Decimal on US. Compare in Decimal so a partial-sell
        # request cannot be rounded up past what is actually held.
        sell_quantity = min(_dec(quantity), held) if quantity else held
        if sell_quantity <= 0:
            return self._outcome(stock_code, success=False, message="Sell quantity resolved to 0")

        try:
            price_info = self.get_current_price(stock_code)
        except BrokerUnavailable as exc:
            return self._outcome(stock_code, success=False, message=f"Failed to get current price: {exc}")

        current_price = (price_info or {}).get("current_price") or 0
        effective_limit = self._tick(limit_price if limit_price and limit_price > 0 else current_price)
        if effective_limit <= 0:
            return self._outcome(stock_code, success=False, message="Failed to get current price")

        return self._submit(
            stock_code,
            side="SELL",
            quantity=sell_quantity,
            limit_price=effective_limit,
            reference_price=current_price,
        )

    # ── US session gating ─────────────────────────────────────────────────────

    US_SESSIONS = ("dayMarket", "preMarket", "regularMarket", "afterMarket")

    def open_us_session(self, *, now: datetime.datetime | None = None) -> str | None:
        """Which US session is open, or None.

        Toss runs four US sessions and publishes all of them in KST, including a
        day market at 09:00–16:50 KST. That matters: the assumption that "the US
        market is shut while PRISM runs" holds for a plain US exchange but not
        here, so the morning batch can in fact trade US names.

        A calendar lookup that fails returns None. Refusing to order beats
        ordering into a session that may not exist, and the caller is told which
        it was.
        """
        moment = now or datetime.datetime.now(KST)
        try:
            calendar = self._client.request(
                "GET", "/api/v1/market-calendar/US", group=ratelimit.DEFAULT
            )
        except (TossApiError, BrokerUnavailable) as exc:
            logger.warning("[TOSS] US market calendar unavailable: %s", exc)
            return None

        if not isinstance(calendar, dict):
            return None

        # Sessions straddle midnight KST, so yesterday's regular session can
        # still be running now; every published day is checked.
        for day_key in ("previousBusinessDay", "today", "nextBusinessDay"):
            day = calendar.get(day_key)
            if not isinstance(day, dict):
                continue
            for session in self.US_SESSIONS:
                window = day.get(session)
                if not isinstance(window, dict):
                    continue
                start, end = window.get("startTime"), window.get("endTime")
                if not start or not end:
                    continue
                try:
                    opens = datetime.datetime.fromisoformat(str(start))
                    closes = datetime.datetime.fromisoformat(str(end))
                except ValueError:
                    continue
                if opens <= moment < closes:
                    return session
        return None

    # ── Fractional quantities ─────────────────────────────────────────────────

    # Toss accepts six decimal places; more is `400 fractional-quantity-scale-exceeded`.
    FRACTIONAL_SCALE = Decimal("0.000001")

    # Fractional orders close an hour before the regular session does.
    FRACTIONAL_CUTOFF = datetime.timedelta(hours=1)

    @staticmethod
    def _is_fractional(quantity: Any) -> bool:
        value = _dec(quantity)
        return value != value.to_integral_value()

    def _round_fractional(self, quantity: Decimal) -> Decimal:
        """Six decimals, always downward.

        Rounding up would ask to sell more than is held, and Toss would reject
        the whole order rather than fill what exists.

        Only quantizes when the value actually exceeds six places. Quantizing
        unconditionally pads trailing zeros — 0.44519 becomes 0.445190 — which
        changes the string sent for a quantity that was already valid.
        """
        if -quantity.as_tuple().exponent > 6:
            return quantity.quantize(self.FRACTIONAL_SCALE, rounding=ROUND_DOWN)
        return quantity

    def fractional_window_open(self, *, now: datetime.datetime | None = None) -> bool:
        """True while Toss accepts fractional quantities.

        Narrower than `open_us_session`: fractional orders are only taken from
        the regular session's open until an hour before its close, so a position
        can be visible and unsellable at the same time. That gap is real and is
        reported rather than worked around.
        """
        moment = now or datetime.datetime.now(KST)
        window = self._regular_session(moment)
        if window is None:
            return False
        opens, closes = window
        return opens <= moment < (closes - self.FRACTIONAL_CUTOFF)

    def _regular_session(
        self, moment: datetime.datetime
    ) -> tuple[datetime.datetime, datetime.datetime] | None:
        """The regular session containing `moment`, if any."""
        try:
            calendar = self._client.request(
                "GET", "/api/v1/market-calendar/US", group=ratelimit.DEFAULT
            )
        except (TossApiError, BrokerUnavailable) as exc:
            logger.warning("[TOSS] US market calendar unavailable: %s", exc)
            return None
        if not isinstance(calendar, dict):
            return None

        for day_key in ("previousBusinessDay", "today", "nextBusinessDay"):
            day = calendar.get(day_key)
            if not isinstance(day, dict):
                continue
            window = day.get("regularMarket")
            if not isinstance(window, dict):
                continue
            start, end = window.get("startTime"), window.get("endTime")
            if not start or not end:
                continue
            try:
                opens = datetime.datetime.fromisoformat(str(start))
                closes = datetime.datetime.fromisoformat(str(end))
            except ValueError:
                continue
            if opens <= moment < closes:
                return opens, closes
        return None

    def _refuse_fractional(
        self, stock_code: str, side: str, quantity: Decimal, reference_price: float
    ) -> dict[str, Any] | None:
        """A refusal outcome when this fractional order cannot be placed."""
        if self.market != "US":
            return self._outcome(
                stock_code, success=False, current_price=reference_price,
                quantity=quantity,
                message=(
                    f"{side} rejected: Toss accepts fractional quantities on US "
                    "orders only; domestic orders must be whole shares."
                ),
            )
        if side != "SELL":
            return self._outcome(
                stock_code, success=False, current_price=reference_price,
                quantity=quantity,
                message=(
                    f"{side} rejected: Toss takes fractional quantity on SELL "
                    "only. A fractional buy is placed by amount (orderAmount)."
                ),
            )
        if not self.fractional_window_open():
            return self._outcome(
                stock_code, success=False, current_price=reference_price,
                quantity=quantity,
                message=(
                    f"{side} rejected: fractional orders are accepted only from "
                    "the regular session open until an hour before its close. "
                    f"{stock_code} holds {quantity} and cannot be sold right now."
                ),
            )
        return None

    def _submit(
        self,
        stock_code: str,
        *,
        side: str,
        quantity: int | Decimal,
        limit_price: int | float,
        reference_price: float,
    ) -> dict[str, Any]:
        fractional = self._is_fractional(quantity)
        if fractional:
            refusal = self._refuse_fractional(
                stock_code, side, _dec(quantity), reference_price
            )
            if refusal is not None:
                logger.info("[TOSS] %s %s fractional refused", side, stock_code)
                return refusal
            quantity = self._round_fractional(_dec(quantity))

        if self.market == "US":
            session = self.open_us_session()
            if session is None:
                # A definite non-placement, reported as a failure rather than
                # raised. Raising from an order method reaches
                # `ExecutionService._execute_submitting_order`, which records
                # UNKNOWN and blocks the position — the opposite of the truth,
                # since this order provably never left the process.
                logger.info(
                    "[TOSS] %s %s refused: no US session open (Toss trades %s)",
                    side, stock_code, ", ".join(self.US_SESSIONS),
                )
                return self._outcome(
                    stock_code,
                    success=False,
                    current_price=reference_price,
                    quantity=quantity,
                    message=(
                        f"{side} rejected: no US session open. Toss accepts orders "
                        f"only during {', '.join(self.US_SESSIONS)}; it has no "
                        f"time-based reserved order to queue this into."
                    ),
                )
            logger.info("[TOSS] %s %s during US %s", side, stock_code, session)

        # A fresh key per attempt, so the client may safely retry a request
        # whose response was lost: Toss replays the original result for ten
        # minutes rather than creating a second order.
        client_order_id = f"prism-{uuid.uuid4().hex[:24]}"

        body = {
            "clientOrderId": client_order_id,
            "symbol": stock_code,
            "side": side,
            # `format(..., "f")` so a Decimal never reaches Toss in
            # scientific notation, which it would reject.
            "quantity": format(Decimal(str(quantity)), "f"),
        }

        if fractional:
            # Toss takes a fractional quantity only on a market order, and
            # rejects the request outright if a price is sent with one.
            body["orderType"] = "MARKET"
            logger.info(
                "[TOSS] %s %s as MARKET for fractional quantity %s",
                side, stock_code, body["quantity"],
            )
        else:
            body["orderType"] = "LIMIT"
            body["price"] = self._format_price(limit_price)

        return self._dispatch(
            body,
            stock_code=stock_code,
            side=side,
            quantity=quantity,
            reference_price=reference_price,
        )

    def _dispatch(
        self,
        body: dict[str, Any],
        *,
        stock_code: str,
        side: str,
        quantity: int | Decimal,
        reference_price: float,
    ) -> dict[str, Any]:
        """Send an order body, read the fill back, and shape the outcome.

        Shared by the quantity-based and amount-based paths so both classify a
        refusal, an ambiguous failure and an unreadable status identically. Two
        copies of this would eventually disagree about which failures are safe
        to retry.
        """
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
        requested_quantity: int | Decimal,
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
        # Exact, not float: this is the number the tracking ledger and the
        # Telegram report will carry, and `int()` here reported a 0.44519-share
        # fill as "0 shares sold".
        filled = _dec(execution.get("filledQuantity"))
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
        quantity = self._settled_quantity(filled, requested_quantity)
        total = float(quantity) * avg_price

        return self._outcome(
            stock_code,
            success=accepted,
            current_price=avg_price or reference_price,
            quantity=quantity,
            total_amount=total,
            order_no=order_id,
            message=(
                f"{side} completed: {quantity} x {avg_price:,.4g} = {total:,.2f}"
                if status == "FILLED"
                else f"{side} accepted (status={status})"
            ),
        )

    def _settled_quantity(
        self, filled: Decimal, requested: int | Decimal
    ) -> int | Decimal:
        """The filled quantity in the type this market uses.

        KR collapses to `int` so downstream arithmetic and formatting are
        unchanged; US keeps the exact decimal, because a fractional fill
        reported as a whole number is how a 0.44-share sale becomes "0 sold".
        """
        if not filled:
            return requested
        if self.market == "KR":
            return int(filled)
        return filled

    def _tick(self, price: float) -> int | float:
        """Coerce a price to what this market can express.

        KR trades in whole won, so an int is right there. US trades in cents,
        and coercing to int would quietly turn $185.50 into $185 — a half-dollar
        below the intended limit on every single US order.
        """
        return int(price) if self.market == "KR" else float(price)

    def _format_price(self, price: int | float) -> str:
        """Match Toss's published precision rules per market.

        KR is whole won. US takes four decimals below $1 and two at or above it,
        and Toss *truncates* rather than rounds — so rounding up here could
        produce a price it then refuses, or one a cent away from intended.
        """
        if self.market == "KR":
            return str(int(price))

        value = Decimal(str(float(price)))
        places = Decimal("0.0001") if value < 1 else Decimal("0.01")
        truncated = value.quantize(places, rounding=ROUND_DOWN)
        return format(truncated.normalize(), "f")

    def _outcome(
        self,
        stock_code: str,
        *,
        success: bool,
        current_price: float = 0,
        quantity: int | Decimal = 0,
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

    def stock_name(self, symbol: str) -> str:
        """Company name, cached.

        `/api/v1/prices` carries no name — only symbol, timestamp, lastPrice and
        currency — so the name comes from `/api/v1/stocks`. Cached because names
        are static and this would otherwise double the calls on a hot path.

        Falls back to the symbol rather than failing: a missing display name
        must not take down a price lookup that a buy is waiting on.
        """
        cached = self._names.get(symbol)
        if cached is not None:
            return cached
        try:
            result = self._client.request(
                "GET", "/api/v1/stocks", params={"symbols": symbol}, group=ratelimit.DEFAULT
            )
        except (TossApiError, BrokerUnavailable) as exc:
            logger.debug("[TOSS] name lookup failed for %s: %s", symbol, exc)
            return symbol

        rows = result if isinstance(result, list) else [result]
        for row in rows:
            if isinstance(row, dict) and str(row.get("symbol")) == symbol:
                name = str(row.get("name") or "").strip()
                if name:
                    self._names[symbol] = name
                    return name
        return symbol

    def get_current_price(self, stock_code: str, *_: Any, **__: Any) -> dict[str, Any] | None:
        """Last price in the shape KIS callers read.

        `change_rate` and `volume` are omitted rather than zeroed. Toss's price
        endpoint does not publish them, and a fabricated 0.0 reads as a real
        flat day on a chart. Leaving the keys out lets a caller's own
        `.get(key, default)` apply instead of asserting something untrue.
        """
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
                "stock_name": self.stock_name(stock_code),
                # KIS reports KR prices as int and callers index arithmetic off
                # that; keeping the type identical avoids surprises downstream.
                "current_price": int(price) if self.market == "KR" else price,
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

    def _quantity(self, raw: Any, symbol: str) -> int | Decimal:
        """Held share count, exactly as the broker reports it.

        KR stays `int` because domestic shares are always whole and callers have
        indexed arithmetic off that type since before this adapter existed.

        US keeps the `Decimal`. Toss US holdings are routinely fractional — a
        real account here holds 0.44519 JEPI — and truncating that to an integer
        turns a live position into zero, which the sell path reads as "you hold
        nothing". A position that cannot be seen cannot be stopped out.
        """
        value = _dec(raw)
        if self.market == "KR":
            if value != value.to_integral_value():
                # Should be unreachable: Toss rejects domestic fractional orders
                # outright. Worth a line if it ever happens rather than a silent
                # truncation.
                logger.warning(
                    "[TOSS] unexpected fractional KR quantity for %s: %s", symbol, value
                )
            return int(value)
        return value

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

            quantity = self._quantity(item.get("quantity"), symbol)
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

    def get_holding_quantity(self, stock_code: str, *_: Any, **__: Any) -> int | Decimal:
        """Held quantity, collapsing an unreadable balance to 0.

        Returns the exact quantity rather than an integer for US, because
        rounding 0.44 down to 0 here would recreate the bug this fix exists to
        remove — a held position reported as none. Callers that can only do
        whole-share arithmetic get a warning rather than a silent wrong answer.
        """
        state, quantity = self.get_holding_quantity_checked(stock_code)
        if state != "HELD" or not quantity:
            return 0
        if isinstance(quantity, Decimal) and quantity != quantity.to_integral_value():
            # The pyramided split-sell path does integer division on this value
            # and would compute a sell quantity of 0. Fractional split selling is
            # out of scope, so make the degradation visible instead of silent.
            logger.warning(
                "[TOSS] %s holds a fractional quantity (%s); whole-share callers "
                "cannot size a partial sell from it",
                stock_code,
                quantity,
            )
        return quantity

    def get_holding_quantity_checked(self, stock_code: str, *_: Any, **__: Any) -> HoldingState:
        authoritative, portfolio = self._portfolio_checked()
        if not authoritative:
            return "UNKNOWN", None
        for holding in portfolio:
            if holding["stock_code"] == stock_code:
                # Pass the quantity through untouched. `int()` here was what made
                # a 0.44-share holding read as FLAT.
                return "HELD", holding["quantity"]
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


def toss_us(client: Any, **kwargs: Any) -> TossBroker:
    """Wrap a Toss client for the US market."""
    return TossBroker(client, market="US", **kwargs)
