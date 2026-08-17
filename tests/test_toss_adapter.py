"""Toss adapter: does it speak the shapes PRISM already reads?

Two kinds of test here. The first checks the translations that are wrong in a
plausible-looking way — a rate off by 100x, a decimal string where a number
belongs, an accepted order reported as a completed fill. The second runs a real
buy → hold → sell cycle through the dry-run simulator, which is Phase 4's
success signal in the PRD.

The contract suite from Phase 1 is reused rather than restated. That reuse is
the point of having written it as a helper: if Toss cannot pass the same checks
KIS passes, the abstraction has not actually been achieved.
"""

from decimal import Decimal

import pytest

from tests.test_broker_contract import (
    assert_holding_state_survives_normalisation,
    assert_order_outcome_shape,
    assert_satisfies_broker_port,
)
from trading.brokers.base import BrokerUnsupported


class StubClient:
    """Canned Toss responses, keyed by (method, path)."""

    account_seq = "acc-1"

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def request(self, method, path, *, params=None, json_body=None, **kwargs):
        self.calls.append((method, path, params, json_body, kwargs))
        key = (method, path)
        if key not in self.responses:
            raise AssertionError(f"unexpected call: {method} {path}")
        value = self.responses[key]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(params, json_body)
        return value

    def get(self, path, *, params=None, **kwargs):
        return self.request("GET", path, params=params, **kwargs)


PRICE_005930 = [{"symbol": "005930", "name": "삼성전자", "lastPrice": "70000",
                 "changeRate": "0.0123", "volume": "1000000"}]

HOLDINGS_ONE = {
    "totalPurchaseAmount": {"krw": "650000", "usd": "0"},
    "marketValue": {"amount": {"krw": "720000", "usd": "0"},
                    "amountAfterCost": {"krw": "705000", "usd": "0"}},
    "profitLoss": {"amount": {"krw": "70000", "usd": "0"}, "rate": "0.1077"},
    "items": [{
        "symbol": "005930", "name": "삼성전자", "marketCountry": "KR", "currency": "KRW",
        "quantity": "10", "lastPrice": "72000", "averagePurchasePrice": "65000",
        "marketValue": {"purchaseAmount": "650000", "amount": "720000"},
        "profitLoss": {"amount": "70000", "rate": "0.1077"},
    }],
}


def filled_order(order_id="ord-1", quantity="10", price="70000", status="FILLED"):
    return {
        "orderId": order_id, "symbol": "005930", "side": "BUY", "orderType": "LIMIT",
        "timeInForce": "DAY", "status": status, "price": price, "quantity": quantity,
        "currency": "KRW", "orderedAt": "2026-08-17T09:30:00+09:00", "canceledAt": None,
        "execution": {"filledQuantity": quantity if status == "FILLED" else "0",
                      "averageFilledPrice": price if status == "FILLED" else None,
                      "filledAmount": "700000", "commission": "0", "tax": "0",
                      "filledAt": "2026-08-17T09:30:01+09:00", "settlementDate": None},
    }


def make_broker(responses=None, **kwargs):
    from trading.brokers.toss.adapter import TossBroker

    client = StubClient(responses)
    return TossBroker(client, market="KR", buy_amount=1_000_000, **kwargs), client


# ── The Phase 1 contract ─────────────────────────────────────────────────────


def test_the_toss_adapter_satisfies_the_same_port_as_kis():
    broker, _ = make_broker()
    assert_satisfies_broker_port(broker)
    assert broker.name == "toss"
    assert broker.market == "KR"


def test_an_invalid_market_is_rejected():
    from trading.brokers.toss.adapter import TossBroker

    with pytest.raises(ValueError):
        TossBroker(StubClient(), market="JP")


# ── Reserved orders ──────────────────────────────────────────────────────────


def test_reserved_orders_raise_unsupported_rather_than_failing_quietly():
    """Toss has none; a failure dict would invite an endless retry."""
    broker, _ = make_broker()

    with pytest.raises(BrokerUnsupported):
        broker.buy_reserved_order("005930", limit_price=70000)
    with pytest.raises(BrokerUnsupported):
        broker.sell_reserved_order("005930", limit_price=70000)


# ── Buying ───────────────────────────────────────────────────────────────────


def test_a_buy_returns_the_outcome_shape_execution_service_reads():
    import asyncio

    broker, _ = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-1", "clientOrderId": "prism-x"},
        ("GET", "/api/v1/orders/ord-1"): filled_order(),
    })

    result = asyncio.run(broker.async_buy_stock("005930"))
    assert_order_outcome_shape(result)
    assert result["success"] is True
    assert result["order_no"] == "ord-1"
    assert result["quantity"] == 10


def test_the_buy_quantity_is_floored_against_the_budget():
    import asyncio

    broker, client = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-1"},
        ("GET", "/api/v1/orders/ord-1"): filled_order(quantity="14"),
    })

    asyncio.run(broker.async_buy_stock("005930", buy_amount=1_000_000))
    posted = next(c[3] for c in client.calls if c[0] == "POST")
    assert posted["quantity"] == "14"  # floor(1_000_000 / 70_000)


def test_an_order_carries_an_idempotency_key():
    """Lets a lost response be retried without creating a second order."""
    import asyncio

    broker, client = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-1"},
        ("GET", "/api/v1/orders/ord-1"): filled_order(),
    })

    asyncio.run(broker.async_buy_stock("005930"))
    post = next(c for c in client.calls if c[0] == "POST")
    assert post[3]["clientOrderId"].startswith("prism-")
    assert post[4]["idempotent"] is True


def test_orders_use_the_order_rate_limit_group():
    """The group that drops to 3/s during the morning batch."""
    import asyncio

    from trading.brokers.toss import ratelimit

    broker, client = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-1"},
        ("GET", "/api/v1/orders/ord-1"): filled_order(),
    })

    asyncio.run(broker.async_buy_stock("005930"))
    post = next(c for c in client.calls if c[0] == "POST")
    assert post[4]["group"] == ratelimit.ORDER


def test_a_price_the_budget_cannot_afford_fails_without_ordering():
    import asyncio

    broker, client = make_broker({("GET", "/api/v1/prices"): PRICE_005930})
    broker.buy_amount = 1000

    result = asyncio.run(broker.async_buy_stock("005930"))
    assert result["success"] is False
    assert "0" in result["message"]
    assert not [c for c in client.calls if c[0] == "POST"]


def test_a_business_refusal_is_a_failure_not_an_unknown():
    """422 order-hours-closed is an answer; marking it unknown blocks the slot."""
    import asyncio

    from trading.brokers.toss.errors import TossApiError

    broker, _ = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): TossApiError(
            "order-hours-closed", "장 운영시간이 아닙니다.", status=422
        ),
    })

    result = asyncio.run(broker.async_buy_stock("005930"))
    assert result["success"] is False
    assert "outcome_unknown" not in result
    assert "order-hours-closed" in result["message"]


def test_an_ambiguous_failure_is_marked_unknown():
    """The order may have landed; plain failure would let the caller re-enter."""
    import asyncio

    from trading.brokers.base import BrokerUnavailable

    broker, _ = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): BrokerUnavailable("connection reset"),
    })

    result = asyncio.run(broker.async_buy_stock("005930"))
    assert result["success"] is False
    assert result["outcome_unknown"] is True


def test_a_timeout_is_marked_unknown():
    import asyncio

    from trading.brokers.toss.adapter import TossBroker

    class SlowClient(StubClient):
        def request(self, *args, **kwargs):
            import time

            time.sleep(0.3)
            return {}

    broker = TossBroker(SlowClient(), market="KR")
    result = asyncio.run(broker.async_buy_stock("005930", timeout=0.01))
    assert result["outcome_unknown"] is True


def test_an_unreadable_order_status_is_unknown_not_success():
    """We know it was submitted; we do not know what happened to it."""
    import asyncio

    from trading.brokers.base import BrokerUnavailable

    broker, _ = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-1"},
        ("GET", "/api/v1/orders/ord-1"): BrokerUnavailable("read timeout"),
    })

    result = asyncio.run(broker.async_buy_stock("005930"))
    assert result["success"] is False
    assert result["outcome_unknown"] is True
    assert result["order_no"] == "ord-1"


def test_a_rejected_order_is_a_definite_failure():
    import asyncio

    broker, _ = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-1"},
        ("GET", "/api/v1/orders/ord-1"): filled_order(status="REJECTED"),
    })

    result = asyncio.run(broker.async_buy_stock("005930"))
    assert result["success"] is False
    assert "outcome_unknown" not in result


def test_a_resting_order_counts_as_accepted():
    """KIS reports an accepted order the same way; tracking reconciles later."""
    import asyncio

    broker, _ = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-1"},
        ("GET", "/api/v1/orders/ord-1"): filled_order(status="PENDING"),
    })

    result = asyncio.run(broker.async_buy_stock("005930"))
    assert result["success"] is True


# ── Selling ──────────────────────────────────────────────────────────────────


def test_a_sell_uses_the_held_quantity_when_none_is_given():
    import asyncio

    broker, client = make_broker({
        ("GET", "/api/v1/holdings"): HOLDINGS_ONE,
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-2"},
        ("GET", "/api/v1/orders/ord-2"): filled_order(order_id="ord-2"),
    })

    asyncio.run(broker.async_sell_stock("005930"))
    posted = next(c[3] for c in client.calls if c[0] == "POST")
    assert posted["quantity"] == "10"
    assert posted["side"] == "SELL"


def test_a_sell_is_capped_at_the_held_quantity():
    import asyncio

    broker, client = make_broker({
        ("GET", "/api/v1/holdings"): HOLDINGS_ONE,
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-2"},
        ("GET", "/api/v1/orders/ord-2"): filled_order(order_id="ord-2"),
    })

    asyncio.run(broker.async_sell_stock("005930", quantity=999))
    posted = next(c[3] for c in client.calls if c[0] == "POST")
    assert posted["quantity"] == "10"


def test_a_sell_refuses_when_the_holding_query_failed():
    """Selling on an unverified position is the mistake three-state prevents."""
    import asyncio

    from trading.brokers.base import BrokerUnavailable

    broker, client = make_broker({("GET", "/api/v1/holdings"): BrokerUnavailable("down")})

    result = asyncio.run(broker.async_sell_stock("005930"))
    assert result["success"] is False
    assert result["outcome_unknown"] is True
    assert not [c for c in client.calls if c[0] == "POST"]


def test_a_sell_with_no_position_does_not_order():
    import asyncio

    broker, client = make_broker({
        ("GET", "/api/v1/holdings"): {"items": []},
    })

    result = asyncio.run(broker.async_sell_stock("005930"))
    assert result["success"] is False
    assert "outcome_unknown" not in result
    assert not [c for c in client.calls if c[0] == "POST"]


# ── Translations that are wrong in a plausible way ───────────────────────────


def test_profit_rate_is_converted_from_fraction_to_percentage():
    """Toss says 0.1077; KIS callers read 10.77. Passing it through is 100x off."""
    broker, _ = make_broker({("GET", "/api/v1/holdings"): HOLDINGS_ONE})

    row = broker.get_portfolio()[0]
    assert row["profit_rate"] == pytest.approx(10.77)


def test_portfolio_rows_use_the_kis_key_names_and_numeric_types():
    broker, _ = make_broker({("GET", "/api/v1/holdings"): HOLDINGS_ONE})

    row = broker.get_portfolio()[0]
    assert set(row) == {
        "stock_code", "stock_name", "quantity", "avg_price",
        "current_price", "eval_amount", "profit_amount", "profit_rate",
    }
    assert isinstance(row["quantity"], int)
    for key in ("avg_price", "current_price", "eval_amount", "profit_amount"):
        assert isinstance(row[key], float), f"{key} left as a string breaks arithmetic"


def test_current_price_matches_the_kis_shape_and_int_type():
    broker, _ = make_broker({("GET", "/api/v1/prices"): PRICE_005930})

    price = broker.get_current_price("005930")
    assert set(price) == {"stock_code", "stock_name", "current_price", "change_rate", "volume"}
    assert isinstance(price["current_price"], int)
    assert price["current_price"] == 70000


def test_account_summary_uses_the_kis_key_names():
    broker, _ = make_broker({
        ("GET", "/api/v1/holdings"): HOLDINGS_ONE,
        ("GET", "/api/v1/buying-power"): {"currency": "KRW", "cashBuyingPower": "5000000"},
    })

    summary = broker.get_account_summary()
    assert set(summary) == {
        "total_eval_amount", "total_profit_amount", "total_profit_rate",
        "deposit", "total_cash", "available_amount",
    }
    assert summary["total_profit_rate"] == pytest.approx(10.77)


def test_a_failed_summary_returns_an_empty_dict_not_none():
    from trading.brokers.base import BrokerUnavailable

    broker, _ = make_broker({("GET", "/api/v1/holdings"): BrokerUnavailable("down")})
    assert broker.get_account_summary() == {}


def test_kr_prices_are_sent_as_whole_won():
    """A fractional won price is rejected by the exchange's tick rules."""
    import asyncio

    broker, client = make_broker({
        ("GET", "/api/v1/prices"): PRICE_005930,
        ("POST", "/api/v1/orders"): {"orderId": "ord-1"},
        ("GET", "/api/v1/orders/ord-1"): filled_order(),
    })

    asyncio.run(broker.async_buy_stock("005930", limit_price=70000))
    posted = next(c[3] for c in client.calls if c[0] == "POST")
    assert posted["price"] == "70000"
    assert "." not in posted["price"]


# ── Three-state holding ──────────────────────────────────────────────────────


def test_a_held_position_reports_held():
    broker, _ = make_broker({("GET", "/api/v1/holdings"): HOLDINGS_ONE})
    assert_holding_state_survives_normalisation(
        broker.get_holding_quantity_checked("005930"), ("HELD", 10)
    )


def test_no_position_reports_flat():
    broker, _ = make_broker({("GET", "/api/v1/holdings"): {"items": []}})
    assert_holding_state_survives_normalisation(
        broker.get_holding_quantity_checked("005930"), ("FLAT", 0)
    )


def test_a_failed_query_reports_unknown_not_flat():
    from trading.brokers.base import BrokerUnavailable

    broker, _ = make_broker({("GET", "/api/v1/holdings"): BrokerUnavailable("down")})
    assert_holding_state_survives_normalisation(
        broker.get_holding_quantity_checked("005930"), ("UNKNOWN", None)
    )


def test_a_malformed_holdings_payload_reports_unknown():
    broker, _ = make_broker({("GET", "/api/v1/holdings"): {"items": "not-a-list"}})
    assert_holding_state_survives_normalisation(
        broker.get_holding_quantity_checked("005930"), ("UNKNOWN", None)
    )


def test_get_holding_quantity_collapses_unknown_to_zero_like_kis():
    from trading.brokers.base import BrokerUnavailable

    broker, _ = make_broker({("GET", "/api/v1/holdings"): BrokerUnavailable("down")})
    assert broker.get_holding_quantity("005930") == 0


# ── Amend / cancel ───────────────────────────────────────────────────────────


def test_amend_and_cancel_report_outcomes():
    broker, client = make_broker({
        ("POST", "/api/v1/orders/ord-1/modify"): {"orderId": "ord-1"},
        ("POST", "/api/v1/orders/ord-1/cancel"): {"orderId": "ord-1"},
    })

    assert broker.amend_order("005930", "ord-1", 71000)["success"] is True
    assert broker.cancel_order("005930", "ord-1")["success"] is True
    assert [c[1] for c in client.calls] == [
        "/api/v1/orders/ord-1/modify", "/api/v1/orders/ord-1/cancel"
    ]


def test_cancelling_a_filled_order_is_a_definite_failure():
    from trading.brokers.toss.errors import TossApiError

    broker, _ = make_broker({
        ("POST", "/api/v1/orders/ord-1/cancel"): TossApiError(
            "order-already-filled", "이미 체결된 주문입니다.", status=422
        ),
    })

    result = broker.cancel_order("005930", "ord-1")
    assert result["success"] is False
    assert "outcome_unknown" not in result


# ── The PRD success signal: a full cycle through the simulator ───────────────


@pytest.fixture
def simulated_broker(tmp_path):
    """A Toss broker whose transport is the dry-run simulator."""
    from trading.brokers.toss.adapter import TossBroker
    from trading.brokers.toss.dryrun import DryRunLedger, DryRunTossClient

    class MarketDataOnly:
        account_seq = "acc-1"

        def __init__(self):
            self.writes = []

        def request(self, method, path, params=None, json_body=None, **kwargs):
            if method != "GET":
                self.writes.append((method, path))
                raise AssertionError(f"a real order escaped: {method} {path}")
            if path.startswith("/api/v1/prices"):
                return [{"symbol": params["symbols"], "name": "삼성전자",
                         "lastPrice": "70000", "changeRate": "0.01", "volume": "1000"}]
            return {}

        def get(self, path, *, params=None, **kwargs):
            return self.request("GET", path, params=params, **kwargs)

    real = MarketDataOnly()
    ledger = DryRunLedger(tmp_path / "cycle.sqlite",
                          initial_cash={"KRW": Decimal("10000000")})
    client = DryRunTossClient(real, ledger=ledger)
    return TossBroker(client, market="KR", buy_amount=1_000_000), real


def test_a_full_buy_hold_sell_cycle_completes_without_a_real_order(simulated_broker):
    """PRD Phase 4 success signal."""
    import asyncio

    broker, real = simulated_broker

    buy = asyncio.run(broker.async_buy_stock("005930"))
    assert buy["success"] is True, buy["message"]
    assert buy["quantity"] == 14

    assert broker.get_holding_quantity_checked("005930") == ("HELD", 14)
    portfolio = broker.get_portfolio()
    assert portfolio[0]["stock_code"] == "005930"
    assert portfolio[0]["quantity"] == 14

    summary = broker.get_account_summary()
    assert summary["total_cash"] == pytest.approx(10_000_000 - 14 * 70_000)

    sell = asyncio.run(broker.async_sell_stock("005930"))
    assert sell["success"] is True, sell["message"]

    assert broker.get_holding_quantity_checked("005930") == ("FLAT", 0)
    assert real.writes == [], "an order reached the real client"


def test_the_simulated_cycle_leaves_cash_whole(simulated_broker):
    import asyncio

    broker, _ = simulated_broker

    asyncio.run(broker.async_buy_stock("005930"))
    asyncio.run(broker.async_sell_stock("005930"))

    assert broker.get_account_summary()["total_cash"] == pytest.approx(10_000_000)
