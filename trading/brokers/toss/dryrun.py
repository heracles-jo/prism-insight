"""Demo mode for a broker that has no demo server.

KIS gives PRISM a paper-trading environment; Toss does not. Without something
in its place, `mode=demo` against Toss would either place real orders or be
refused entirely, and neither lets anyone exercise the pipeline before trusting
it with money.

So this stands where the KIS paper server would: it answers as the broker, using
real market data for prices and a local ledger for the account. Everything above
it — the adapter, `ExecutionService`, the tracking agent — runs its real code
path and cannot tell the difference. That is the point. A simulator that the
callers know about only proves the simulator works.

Interception is at the HTTP boundary rather than above the adapter for the same
reason: the adapter's own logic (quantity maths, price fallbacks, error
handling) is exactly what demo mode needs to exercise.

**The routing table is default-deny.** An unrecognised mutating request is
blocked, never forwarded. Toss will add endpoints, and the failure mode of an
allow-list that falls through is a real order placed from a run someone believed
was a simulation. Blocking something harmless is a bug report; forwarding
something dangerous is a loss.

Fees are simulated as zero unless configured. Inventing Toss's real schedule
would produce authoritative-looking numbers that are wrong; a visible zero is
easier to distrust than a plausible fabrication.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3
import threading
import uuid
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trading.brokers.base import BrokerUnavailable, BrokerUnsupported
from trading.brokers.toss.errors import TossApiError

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# Anchored to the repo root, not the CWD: a cron started from another
# directory used to open a fresh, empty simulated ledger.
DEFAULT_DB_PATH = str(Path(__file__).resolve().parents[3] / "toss_dryrun.sqlite")
DEFAULT_CASH = {"KRW": Decimal("10000000"), "USD": Decimal("10000")}

# Read paths whose answers must come from the real API: market data is what
# makes a simulated fill realistic, and none of it can move money.
_PASSTHROUGH_PREFIXES = (
    "/api/v1/prices",
    "/api/v1/candles",
    "/api/v1/orderbook",
    "/api/v1/trades",
    "/api/v1/price-limits",
    "/api/v1/stocks",
    "/api/v1/market-indicators",
    "/api/v1/market-calendar",
    "/api/v1/exchange-rate",
    "/api/v1/rankings",
    "/api/v1/commissions",
    "/api/v1/accounts",
)

# Read paths that describe the account, and so must reflect the simulation
# rather than the real portfolio. Serving the real ones here would be incoherent:
# a simulated buy would never appear, and the caller would treat it as a failure.
_SIMULATED_READ_PREFIXES = (
    "/api/v1/holdings",
    "/api/v1/buying-power",
    "/api/v1/sellable-quantity",
    "/api/v1/orders",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS toss_dryrun_orders (
    order_id TEXT PRIMARY KEY,
    client_order_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    time_in_force TEXT NOT NULL,
    status TEXT NOT NULL,
    price TEXT,
    quantity TEXT NOT NULL,
    currency TEXT NOT NULL,
    ordered_at TEXT NOT NULL,
    canceled_at TEXT,
    filled_quantity TEXT NOT NULL DEFAULT '0',
    average_filled_price TEXT,
    filled_at TEXT
);

CREATE TABLE IF NOT EXISTS toss_dryrun_positions (
    symbol TEXT PRIMARY KEY,
    currency TEXT NOT NULL,
    quantity TEXT NOT NULL,
    average_price TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS toss_dryrun_cash (
    currency TEXT PRIMARY KEY,
    amount TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_toss_dryrun_orders_client
    ON toss_dryrun_orders(client_order_id);
"""


def _now_kst_iso() -> str:
    return datetime.datetime.now(KST).isoformat()


def _dec(value: Any, default: str = "0") -> Decimal:
    """Parse a Toss decimal-as-string without letting bad input raise."""
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _plain(value: Decimal) -> str:
    """Render as Toss does: a plain decimal string, never scientific notation."""
    return format(value.normalize(), "f")


def _currency_for(symbol: str) -> str:
    """KRX symbols are six digits; US symbols are alphabetic tickers."""
    return "KRW" if symbol.isdigit() and len(symbol) == 6 else "USD"


class DryRunLedger:
    """The simulated account: cash, positions, and orders.

    Kept in its own database rather than PRISM's tracking tables on purpose.
    `stock_holdings` is PRISM's *view* of what the broker reports, written by the
    tracking agent from broker responses. Writing simulated fills there too would
    have two writers on one table and corrupt real bookkeeping. Under KIS the
    paper server holds this state remotely; here it is local, but it is still
    broker state, not PRISM state.
    """

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH, *, initial_cash: dict[str, Decimal] | None = None):
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._initial_cash = dict(initial_cash or DEFAULT_CASH)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ── Cash ──────────────────────────────────────────────────────────────────

    def cash(self, currency: str) -> Decimal:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT amount FROM toss_dryrun_cash WHERE currency = ?", (currency,)
            ).fetchone()
            if row is None:
                seed = self._initial_cash.get(currency, Decimal("0"))
                conn.execute(
                    "INSERT INTO toss_dryrun_cash (currency, amount) VALUES (?, ?)",
                    (currency, _plain(seed)),
                )
                return seed
            return _dec(row["amount"])

    def _set_cash(self, conn: sqlite3.Connection, currency: str, amount: Decimal) -> None:
        conn.execute(
            "INSERT INTO toss_dryrun_cash (currency, amount) VALUES (?, ?) "
            "ON CONFLICT(currency) DO UPDATE SET amount = excluded.amount",
            (currency, _plain(amount)),
        )

    # ── Positions ─────────────────────────────────────────────────────────────

    def position(self, symbol: str) -> tuple[Decimal, Decimal]:
        """(quantity, average price) — zeroes when flat."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT quantity, average_price FROM toss_dryrun_positions WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        if row is None:
            return Decimal("0"), Decimal("0")
        return _dec(row["quantity"]), _dec(row["average_price"])

    def positions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, currency, quantity, average_price FROM toss_dryrun_positions "
                "WHERE CAST(quantity AS REAL) > 0 ORDER BY symbol"
            ).fetchall()
        return [dict(row) for row in rows]

    # ── Fills ─────────────────────────────────────────────────────────────────

    def apply_fill(self, *, symbol: str, side: str, quantity: Decimal, price: Decimal, currency: str) -> None:
        """Move cash and position as a fill would.

        Raises `TossApiError` with the code Toss itself would use when the
        account cannot support the order, so callers exercise the same branch
        they would in production.
        """
        with self._lock, self._connect() as conn:
            held_row = conn.execute(
                "SELECT quantity, average_price FROM toss_dryrun_positions WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            held = _dec(held_row["quantity"]) if held_row else Decimal("0")
            avg = _dec(held_row["average_price"]) if held_row else Decimal("0")

            cash_row = conn.execute(
                "SELECT amount FROM toss_dryrun_cash WHERE currency = ?", (currency,)
            ).fetchone()
            cash = (
                _dec(cash_row["amount"])
                if cash_row
                else self._initial_cash.get(currency, Decimal("0"))
            )

            notional = quantity * price

            if side == "BUY":
                if notional > cash:
                    raise TossApiError(
                        "insufficient-buying-power",
                        f"모의 잔고 부족: 필요 {_plain(notional)} {currency}, 보유 {_plain(cash)}",
                        status=422,
                    )
                new_quantity = held + quantity
                new_avg = ((held * avg) + notional) / new_quantity if new_quantity else Decimal("0")
                self._set_cash(conn, currency, cash - notional)
            else:
                if quantity > held:
                    raise TossApiError(
                        "insufficient-quantity",
                        f"모의 보유수량 부족: 매도 {_plain(quantity)}, 보유 {_plain(held)}",
                        status=422,
                    )
                new_quantity = held - quantity
                new_avg = avg if new_quantity else Decimal("0")
                self._set_cash(conn, currency, cash + notional)

            conn.execute(
                "INSERT INTO toss_dryrun_positions (symbol, currency, quantity, average_price) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(symbol) DO UPDATE SET "
                "quantity = excluded.quantity, average_price = excluded.average_price, "
                "currency = excluded.currency",
                (symbol, currency, _plain(new_quantity), _plain(new_avg)),
            )

    # ── Orders ────────────────────────────────────────────────────────────────

    def record_order(self, order: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO toss_dryrun_orders (order_id, client_order_id, symbol, side, "
                "order_type, time_in_force, status, price, quantity, currency, ordered_at, "
                "canceled_at, filled_quantity, average_filled_price, filled_at) "
                "VALUES (:order_id, :client_order_id, :symbol, :side, :order_type, "
                ":time_in_force, :status, :price, :quantity, :currency, :ordered_at, "
                ":canceled_at, :filled_quantity, :average_filled_price, :filled_at)",
                order,
            )

    def order(self, order_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM toss_dryrun_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
        return dict(row) if row else None

    def order_by_client_id(self, client_order_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM toss_dryrun_orders WHERE client_order_id = ? "
                "ORDER BY ordered_at DESC LIMIT 1",
                (client_order_id,),
            ).fetchone()
        return dict(row) if row else None

    def orders(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM toss_dryrun_orders ORDER BY ordered_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def update_order(self, order_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE toss_dryrun_orders SET {assignments} WHERE order_id = ?",
                (*fields.values(), order_id),
            )

    def reset(self) -> None:
        """Empty the simulated account. For tests and for starting a fresh run."""
        with self._connect() as conn:
            conn.execute("DELETE FROM toss_dryrun_orders")
            conn.execute("DELETE FROM toss_dryrun_positions")
            conn.execute("DELETE FROM toss_dryrun_cash")


class DryRunTossClient:
    """A `TossClient` that never places an order.

    Duck-typed against the real client so it can be swapped in at construction
    time; nothing downstream branches on which one it holds.
    """

    def __init__(self, real_client: Any, *, ledger: DryRunLedger | None = None):
        self._real = real_client
        self._ledger = ledger if ledger is not None else DryRunLedger()
        self.order_calls_blocked = 0
        logger.warning(
            "[TOSS_DRYRUN] simulation active — no order will reach Toss (ledger=%s)",
            self._ledger.db_path,
        )

    @property
    def ledger(self) -> DryRunLedger:
        return self._ledger

    @property
    def account_seq(self) -> str | None:
        return getattr(self._real, "account_seq", None)

    # ── Verbs, mirroring TossClient ───────────────────────────────────────────

    def get(self, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request("GET", path, params=params, **kwargs)

    def post(self, path: str, *, json_body: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request("POST", path, json_body=json_body, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        verb = method.upper()

        if verb == "GET":
            if path.startswith(_SIMULATED_READ_PREFIXES):
                return self._simulated_read(path, params or {})
            if path.startswith(_PASSTHROUGH_PREFIXES):
                return self._real.request(verb, path, params=params, **kwargs)
            # An unknown read is harmless to forward, and refusing it would make
            # demo mode less capable than production for no safety gain.
            logger.debug("[TOSS_DRYRUN] forwarding unrecognised read %s", path)
            return self._real.request(verb, path, params=params, **kwargs)

        return self._intercept_write(verb, path, json_body or {})

    # ── Writes ────────────────────────────────────────────────────────────────

    def _intercept_write(self, verb: str, path: str, body: dict[str, Any]) -> Any:
        self.order_calls_blocked += 1

        if path == "/api/v1/orders" and verb == "POST":
            return self._simulate_new_order(body)
        if path.startswith("/api/v1/orders/") and path.endswith("/cancel"):
            return self._simulate_cancel(path.split("/")[4])
        if path.startswith("/api/v1/orders/") and path.endswith("/modify"):
            return self._simulate_modify(path.split("/")[4], body)
        if path.startswith("/api/v1/conditional-orders"):
            raise BrokerUnsupported(
                "conditional orders are out of scope for v1; not simulated"
            )

        # Default deny. Anything mutating that is not explicitly simulated must
        # not reach Toss from a run the operator believes is a simulation.
        logger.error(
            "[TOSS_DRYRUN] blocked unrecognised %s %s — dry run forwards no writes",
            verb,
            path,
        )
        raise BrokerUnsupported(
            f"dry run blocked an unsimulated write: {verb} {path}"
        )

    def _simulate_new_order(self, body: dict[str, Any]) -> dict[str, Any]:
        symbol = str(body.get("symbol") or "")
        side = str(body.get("side") or "").upper()
        order_type = str(body.get("orderType") or "").upper()

        if not symbol or side not in {"BUY", "SELL"} or order_type not in {"LIMIT", "MARKET"}:
            raise TossApiError(
                "invalid-request", "symbol, side, orderType are required", status=400
            )

        currency = _currency_for(symbol)
        market_price = self._market_price(symbol)

        order_amount = body.get("orderAmount")
        if order_amount is not None:
            # Amount-based: the money is fixed and the share count follows from
            # the fill price, which is the opposite of a quantity order. Toss
            # takes these on US market orders only.
            if body.get("quantity") is not None:
                raise TossApiError(
                    "invalid-request",
                    "send exactly one of quantity or orderAmount",
                    status=400,
                )
            if currency != "USD" or order_type != "MARKET":
                raise TossApiError(
                    "invalid-request",
                    "orderAmount is accepted on US market orders only",
                    status=400,
                )
            spend = _dec(order_amount)
            if spend <= 0:
                raise TossApiError(
                    "invalid-request", "orderAmount must be positive", status=400
                )
            if market_price <= 0:
                raise BrokerUnavailable(f"no usable price for {symbol}")
            # Six decimals, matching what Toss will report back.
            quantity = (spend / market_price).quantize(
                Decimal("0.000001"), rounding=ROUND_DOWN
            )
            if quantity <= 0:
                raise TossApiError(
                    "invalid-request",
                    "orderAmount buys less than the minimum fraction",
                    status=400,
                )
        else:
            quantity = _dec(body.get("quantity"))
            if quantity <= 0:
                raise TossApiError(
                    "invalid-request", "quantity must be positive", status=400
                )
        limit_price = _dec(body.get("price")) if body.get("price") is not None else None

        if order_type == "LIMIT" and limit_price is None:
            raise TossApiError("invalid-request", "price is required for LIMIT", status=400)

        fill_price, fills = self._decide_fill(side, order_type, limit_price, market_price)

        order_id = f"dryrun-{uuid.uuid4().hex}"
        now = _now_kst_iso()

        if fills:
            # Raises on insufficient cash/quantity, so the caller meets the same
            # refusal it would get from Toss.
            self._ledger.apply_fill(
                symbol=symbol, side=side, quantity=quantity, price=fill_price, currency=currency
            )

        self._ledger.record_order(
            {
                "order_id": order_id,
                "client_order_id": body.get("clientOrderId"),
                "symbol": symbol,
                "side": side,
                "order_type": order_type,
                "time_in_force": str(body.get("timeInForce") or "DAY"),
                "status": "FILLED" if fills else "PENDING",
                "price": _plain(limit_price) if limit_price is not None else None,
                "quantity": _plain(quantity),
                "currency": currency,
                "ordered_at": now,
                "canceled_at": None,
                "filled_quantity": _plain(quantity) if fills else "0",
                "average_filled_price": _plain(fill_price) if fills else None,
                "filled_at": now if fills else None,
            }
        )

        logger.info(
            "[TOSS_DRYRUN] simulated %s %s %s @ %s (%s)",
            side,
            _plain(quantity),
            symbol,
            _plain(fill_price) if fills else "unfilled",
            "FILLED" if fills else "PENDING",
        )

        return {"orderId": order_id, "clientOrderId": body.get("clientOrderId")}

    def _decide_fill(
        self, side: str, order_type: str, limit_price: Decimal | None, market_price: Decimal
    ) -> tuple[Decimal, bool]:
        """Fill a marketable order, leave the rest resting.

        Filling every limit order regardless of price would make demo mode agree
        with any strategy, including one whose limits are nowhere near the book.
        """
        if order_type == "MARKET":
            return market_price, True
        assert limit_price is not None
        if side == "BUY":
            return (limit_price, limit_price >= market_price)
        return (limit_price, limit_price <= market_price)

    def _simulate_cancel(self, order_id: str) -> dict[str, Any]:
        order = self._ledger.order(order_id)
        if order is None:
            raise TossApiError("order-not-found", f"unknown order {order_id}", status=404)
        if order["status"] == "FILLED":
            raise TossApiError(
                "order-already-filled", "cannot cancel a filled order", status=422
            )
        self._ledger.update_order(order_id, status="CANCELED", canceled_at=_now_kst_iso())
        return {"orderId": order_id, "clientOrderId": order["client_order_id"]}

    def _simulate_modify(self, order_id: str, body: dict[str, Any]) -> dict[str, Any]:
        order = self._ledger.order(order_id)
        if order is None:
            raise TossApiError("order-not-found", f"unknown order {order_id}", status=404)
        if order["status"] == "FILLED":
            raise TossApiError(
                "order-already-filled", "cannot modify a filled order", status=422
            )
        updates: dict[str, Any] = {}
        if body.get("price") is not None:
            updates["price"] = _plain(_dec(body["price"]))
        if body.get("quantity") is not None:
            updates["quantity"] = _plain(_dec(body["quantity"]))
        self._ledger.update_order(order_id, **updates)
        return {"orderId": order_id, "clientOrderId": order["client_order_id"]}

    # ── Reads served from the ledger ──────────────────────────────────────────

    def _simulated_read(self, path: str, params: dict[str, Any]) -> Any:
        if path.startswith("/api/v1/holdings"):
            return self._holdings()
        if path.startswith("/api/v1/buying-power"):
            currency = str(params.get("currency") or "KRW").upper()
            return {
                "currency": currency,
                "cashBuyingPower": _plain(self._ledger.cash(currency)),
            }
        if path.startswith("/api/v1/sellable-quantity"):
            quantity, _ = self._ledger.position(str(params.get("symbol") or ""))
            return {"sellableQuantity": _plain(quantity)}
        if path == "/api/v1/orders":
            return [self._order_detail(o) for o in self._ledger.orders()]
        if path.startswith("/api/v1/orders/"):
            order_id = path.split("/")[4]
            order = self._ledger.order(order_id)
            if order is None:
                raise TossApiError("order-not-found", f"unknown order {order_id}", status=404)
            return self._order_detail(order)
        raise BrokerUnavailable(f"dry run has no answer for {path}")

    def _holdings(self) -> dict[str, Any]:
        """Emit the documented HoldingsOverview shape.

        Kept faithful — including numbers as strings — because Phase 4's adapter
        will be written against this, and a simulator with a convenient shape
        would push the mismatch into production instead of catching it here.
        """
        items = []
        totals = {"KRW": Decimal("0"), "USD": Decimal("0")}
        purchases = {"KRW": Decimal("0"), "USD": Decimal("0")}

        for row in self._ledger.positions():
            symbol = row["symbol"]
            currency = row["currency"]
            quantity = _dec(row["quantity"])
            avg = _dec(row["average_price"])
            last = self._market_price(symbol, fallback=avg)

            market_value = quantity * last
            purchase = quantity * avg
            totals[currency] = totals.get(currency, Decimal("0")) + market_value
            purchases[currency] = purchases.get(currency, Decimal("0")) + purchase
            profit = market_value - purchase

            items.append(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "marketCountry": "KR" if currency == "KRW" else "US",
                    "currency": currency,
                    "quantity": _plain(quantity),
                    "lastPrice": _plain(last),
                    "averagePurchasePrice": _plain(avg),
                    "marketValue": {
                        "purchaseAmount": _plain(purchase),
                        "amount": _plain(market_value),
                        "amountAfterCost": _plain(market_value),
                    },
                    "profitLoss": {
                        "amount": _plain(profit),
                        "amountAfterCost": _plain(profit),
                        "rate": _plain(profit / purchase) if purchase else "0",
                        "rateAfterCost": _plain(profit / purchase) if purchase else "0",
                    },
                    "dailyProfitLoss": {"amount": "0", "rate": "0"},
                    # Zero rather than a guess at Toss's real schedule.
                    "cost": {"commission": "0", "tax": "0"},
                }
            )

        total_purchase = sum(purchases.values(), Decimal("0"))
        total_profit = sum(totals.values(), Decimal("0")) - total_purchase

        return {
            "totalPurchaseAmount": {k: _plain(v) for k, v in purchases.items()},
            "marketValue": {
                "amount": {k: _plain(v) for k, v in totals.items()},
                "amountAfterCost": {k: _plain(v) for k, v in totals.items()},
            },
            "profitLoss": {
                "amount": {k: _plain(v - purchases[k]) for k, v in totals.items()},
                "amountAfterCost": {k: _plain(v - purchases[k]) for k, v in totals.items()},
                "rate": _plain(total_profit / total_purchase) if total_purchase else "0",
                "rateAfterCost": _plain(total_profit / total_purchase) if total_purchase else "0",
            },
            "dailyProfitLoss": {"amount": {"krw": "0", "usd": "0"}, "rate": "0"},
            "items": items,
        }

    def _order_detail(self, order: dict[str, Any]) -> dict[str, Any]:
        filled = _dec(order["filled_quantity"])
        avg = order["average_filled_price"]
        return {
            "orderId": order["order_id"],
            "symbol": order["symbol"],
            "side": order["side"],
            "orderType": order["order_type"],
            "timeInForce": order["time_in_force"],
            "status": order["status"],
            "price": order["price"],
            "quantity": order["quantity"],
            "orderAmount": None,
            "currency": order["currency"],
            "orderedAt": order["ordered_at"],
            "canceledAt": order["canceled_at"],
            "execution": {
                "filledQuantity": order["filled_quantity"],
                "averageFilledPrice": avg,
                "filledAmount": _plain(filled * _dec(avg)) if avg else "0",
                "commission": "0",
                "tax": "0",
                "filledAt": order["filled_at"],
                "settlementDate": None,
            },
        }

    # ── Prices ────────────────────────────────────────────────────────────────

    def _market_price(self, symbol: str, *, fallback: Decimal | None = None) -> Decimal:
        """Real last price, so simulated fills use real numbers."""
        try:
            result = self._real.get("/api/v1/prices", params={"symbols": symbol})
        except Exception as exc:  # noqa: BLE001 - price lookup must not mask the simulation
            if fallback is not None:
                logger.warning(
                    "[TOSS_DRYRUN] price lookup failed for %s (%s); using %s",
                    symbol,
                    exc,
                    _plain(fallback),
                )
                return fallback
            raise BrokerUnavailable(
                f"dry run could not price {symbol}: {exc}"
            ) from exc

        rows = result if isinstance(result, list) else [result]
        for row in rows:
            if isinstance(row, dict) and str(row.get("symbol")) == symbol:
                price = _dec(row.get("lastPrice"))
                if price > 0:
                    return price

        if fallback is not None:
            return fallback
        raise BrokerUnavailable(f"dry run got no usable price for {symbol}")
