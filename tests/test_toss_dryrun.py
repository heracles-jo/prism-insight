"""Dry run: the only test that really matters is that nothing gets ordered.

Toss has no paper-trading server, so this stands in for one. Every test here
uses a real-client stub that records every call it receives, and the central
assertion is that no write ever reaches it. A simulator that mostly works is
worth nothing — the one case it forwards is a real order placed from a run
somebody believed was a simulation.
"""

from decimal import Decimal

import pytest

from trading.brokers.base import BrokerUnsupported


class SpyRealClient:
    """Records everything, and screams if asked to mutate."""

    account_seq = "acc-1"

    def __init__(self, prices=None):
        self.calls = []
        self.prices = prices or {"005930": "70000", "AAPL": "185.5"}

    def request(self, method, path, params=None, json_body=None, **kwargs):
        self.calls.append((method, path, params, json_body))
        if method != "GET":
            raise AssertionError(f"dry run forwarded a write to Toss: {method} {path}")
        if path.startswith("/api/v1/prices"):
            symbol = (params or {}).get("symbols")
            if symbol in self.prices:
                return [{"symbol": symbol, "lastPrice": self.prices[symbol], "currency": "KRW"}]
            return []
        return {"passthrough": path}

    def get(self, path, *, params=None, **kwargs):
        return self.request("GET", path, params=params, **kwargs)

    @property
    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


@pytest.fixture
def dryrun(tmp_path):
    from trading.brokers.toss.dryrun import DryRunLedger, DryRunTossClient

    real = SpyRealClient()
    ledger = DryRunLedger(
        tmp_path / "dryrun.sqlite",
        initial_cash={"KRW": Decimal("10000000"), "USD": Decimal("10000")},
    )
    return DryRunTossClient(real, ledger=ledger), real, ledger


# ── The central guarantee ────────────────────────────────────────────────────


def test_placing_an_order_sends_nothing_to_toss(dryrun):
    client, real, _ = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })

    assert real.writes == [], "an order reached the network in dry run"


def test_a_full_buy_sell_cycle_sends_nothing_to_toss(dryrun):
    client, real, _ = dryrun

    buy = client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })
    client.get("/api/v1/holdings")
    client.get("/api/v1/sellable-quantity", params={"symbol": "005930"})
    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "SELL", "orderType": "MARKET", "quantity": "10"
    })
    client.get(f"/api/v1/orders/{buy['orderId']}")

    assert real.writes == []


def test_an_unrecognised_write_is_blocked_not_forwarded(dryrun):
    """Toss will add endpoints; falling through would place a real order."""
    client, real, _ = dryrun

    with pytest.raises(BrokerUnsupported):
        client.post("/api/v1/some-future-write", json_body={"anything": 1})

    assert real.writes == []


@pytest.mark.parametrize("verb", ["POST", "PUT", "PATCH", "DELETE"])
def test_every_mutating_verb_defaults_to_denied(dryrun, verb):
    client, real, _ = dryrun

    with pytest.raises(BrokerUnsupported):
        client.request(verb, "/api/v1/unknown-thing", json_body={})

    assert real.writes == []


def test_conditional_orders_are_refused_rather_than_silently_ignored(dryrun):
    client, real, _ = dryrun

    with pytest.raises(BrokerUnsupported):
        client.post("/api/v1/conditional-orders", json_body={"symbol": "005930"})
    assert real.writes == []


# ── Market data still comes from the real API ────────────────────────────────


def test_market_data_is_not_simulated(dryrun):
    """A simulation on fake prices proves nothing about the strategy."""
    client, real, _ = dryrun

    client.get("/api/v1/candles", params={"symbol": "005930"})
    client.get("/api/v1/orderbook", params={"symbol": "005930"})

    forwarded = [c[1] for c in real.calls]
    assert "/api/v1/candles" in forwarded
    assert "/api/v1/orderbook" in forwarded


def test_fills_use_the_real_market_price(dryrun):
    client, _, ledger = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })

    quantity, avg = ledger.position("005930")
    assert quantity == Decimal("10")
    assert avg == Decimal("70000")


# ── Account reads reflect the simulation, not the real account ───────────────


def test_holdings_reflect_simulated_fills(dryrun):
    client, _, _ = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })
    holdings = client.get("/api/v1/holdings")

    symbols = [item["symbol"] for item in holdings["items"]]
    assert symbols == ["005930"]
    assert holdings["items"][0]["quantity"] == "10"


def test_holdings_are_not_passed_through_to_the_real_account(dryrun):
    """Otherwise a simulated buy never appears and reads as a failed order."""
    client, real, _ = dryrun

    client.get("/api/v1/holdings")
    assert "/api/v1/holdings" not in [c[1] for c in real.calls]


def test_buying_power_falls_as_cash_is_spent(dryrun):
    client, _, _ = dryrun

    before = Decimal(client.get("/api/v1/buying-power", params={"currency": "KRW"})["cashBuyingPower"])
    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })
    after = Decimal(client.get("/api/v1/buying-power", params={"currency": "KRW"})["cashBuyingPower"])

    assert before - after == Decimal("700000")


def test_selling_returns_cash_and_clears_the_position(dryrun):
    client, _, ledger = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })
    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "SELL", "orderType": "MARKET", "quantity": "10"
    })

    quantity, _ = ledger.position("005930")
    assert quantity == Decimal("0")
    assert ledger.cash("KRW") == Decimal("10000000")


def test_sellable_quantity_tracks_the_position(dryrun):
    client, _, _ = dryrun

    assert client.get("/api/v1/sellable-quantity", params={"symbol": "005930"})["sellableQuantity"] == "0"
    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "7"
    })
    assert client.get("/api/v1/sellable-quantity", params={"symbol": "005930"})["sellableQuantity"] == "7"


def test_average_price_blends_across_two_buys(dryrun):
    client, real, ledger = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })
    real.prices["005930"] = "90000"
    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })

    quantity, avg = ledger.position("005930")
    assert quantity == Decimal("20")
    assert avg == Decimal("80000")


# ── Refusals the caller must be able to exercise ─────────────────────────────


def test_a_buy_beyond_the_simulated_cash_is_refused(dryrun):
    from trading.brokers.toss.errors import TossApiError

    client, _, _ = dryrun

    with pytest.raises(TossApiError) as excinfo:
        client.post("/api/v1/orders", json_body={
            "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "1000"
        })
    assert excinfo.value.code == "insufficient-buying-power"


def test_selling_more_than_held_is_refused(dryrun):
    from trading.brokers.toss.errors import TossApiError

    client, _, _ = dryrun

    with pytest.raises(TossApiError) as excinfo:
        client.post("/api/v1/orders", json_body={
            "symbol": "005930", "side": "SELL", "orderType": "MARKET", "quantity": "5"
        })
    assert excinfo.value.code == "insufficient-quantity"


def test_an_amount_based_order_buys_a_fraction(dryrun):
    """orderAmount fixes the money and lets the share count follow the price."""
    client, _, ledger = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "AAPL", "side": "BUY", "orderType": "MARKET", "orderAmount": "100.00"
    })

    quantity, _ = ledger.position("AAPL")
    # 100.00 / 185.5, truncated to six places.
    assert quantity == Decimal("0.539083")


def test_an_amount_order_needs_a_market_order(dryrun):
    from trading.brokers.toss.errors import TossApiError

    client, _, _ = dryrun

    with pytest.raises(TossApiError):
        client.post("/api/v1/orders", json_body={
            "symbol": "AAPL", "side": "BUY", "orderType": "LIMIT",
            "orderAmount": "100.00", "price": "185.5",
        })


def test_an_amount_order_is_refused_for_domestic(dryrun):
    """Toss takes amount-based orders on US stocks only."""
    from trading.brokers.toss.errors import TossApiError

    client, _, _ = dryrun

    with pytest.raises(TossApiError):
        client.post("/api/v1/orders", json_body={
            "symbol": "005930", "side": "BUY", "orderType": "MARKET",
            "orderAmount": "100000",
        })


def test_quantity_and_amount_are_mutually_exclusive(dryrun):
    from trading.brokers.toss.errors import TossApiError

    client, _, _ = dryrun

    with pytest.raises(TossApiError):
        client.post("/api/v1/orders", json_body={
            "symbol": "AAPL", "side": "BUY", "orderType": "MARKET",
            "quantity": "1", "orderAmount": "100.00",
        })


def test_a_limit_order_without_a_price_is_rejected(dryrun):
    from trading.brokers.toss.errors import TossApiError

    client, _, _ = dryrun

    with pytest.raises(TossApiError):
        client.post("/api/v1/orders", json_body={
            "symbol": "005930", "side": "BUY", "orderType": "LIMIT", "quantity": "1"
        })


# ── Limit orders only fill when marketable ───────────────────────────────────


def test_an_unmarketable_limit_buy_rests_instead_of_filling(dryrun):
    """Filling any limit would make demo mode agree with any strategy."""
    client, _, ledger = dryrun

    order = client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "LIMIT",
        "quantity": "10", "price": "50000",
    })

    assert client.get(f"/api/v1/orders/{order['orderId']}")["status"] == "PENDING"
    assert ledger.position("005930")[0] == Decimal("0")


def test_a_marketable_limit_buy_fills(dryrun):
    client, _, ledger = dryrun

    order = client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "LIMIT",
        "quantity": "10", "price": "72000",
    })

    assert client.get(f"/api/v1/orders/{order['orderId']}")["status"] == "FILLED"
    assert ledger.position("005930")[0] == Decimal("10")


def test_an_unmarketable_limit_sell_rests(dryrun):
    client, _, _ = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })
    order = client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "SELL", "orderType": "LIMIT",
        "quantity": "10", "price": "90000",
    })

    assert client.get(f"/api/v1/orders/{order['orderId']}")["status"] == "PENDING"


# ── Order lifecycle ──────────────────────────────────────────────────────────


def test_a_resting_order_can_be_cancelled(dryrun):
    client, real, _ = dryrun

    order = client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "LIMIT",
        "quantity": "10", "price": "50000",
    })
    client.post(f"/api/v1/orders/{order['orderId']}/cancel")

    detail = client.get(f"/api/v1/orders/{order['orderId']}")
    assert detail["status"] == "CANCELED"
    assert detail["canceledAt"] is not None
    assert real.writes == []


def test_a_filled_order_cannot_be_cancelled(dryrun):
    from trading.brokers.toss.errors import TossApiError

    client, _, _ = dryrun

    order = client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })
    with pytest.raises(TossApiError) as excinfo:
        client.post(f"/api/v1/orders/{order['orderId']}/cancel")
    assert excinfo.value.code == "order-already-filled"


def test_a_resting_order_can_be_modified(dryrun):
    client, _, _ = dryrun

    order = client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "LIMIT",
        "quantity": "10", "price": "50000",
    })
    client.post(f"/api/v1/orders/{order['orderId']}/modify", json_body={"price": "60000"})

    assert client.get(f"/api/v1/orders/{order['orderId']}")["price"] == "60000"


def test_cancelling_an_unknown_order_is_a_404(dryrun):
    from trading.brokers.toss.errors import TossApiError

    client, _, _ = dryrun
    with pytest.raises(TossApiError) as excinfo:
        client.post("/api/v1/orders/nope/cancel")
    assert excinfo.value.status == 404


def test_the_order_list_is_served_from_the_ledger(dryrun):
    client, _, _ = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "1"
    })
    orders = client.get("/api/v1/orders")

    assert len(orders) == 1
    assert orders[0]["symbol"] == "005930"


# ── Response shape fidelity ──────────────────────────────────────────────────


def test_order_creation_returns_the_documented_shape(dryrun):
    client, _, _ = dryrun

    result = client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET",
        "quantity": "1", "clientOrderId": "my-order-001",
    })

    assert set(result) == {"orderId", "clientOrderId"}
    assert result["clientOrderId"] == "my-order-001"


def test_order_detail_matches_the_documented_shape(dryrun):
    client, _, _ = dryrun

    order = client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })
    detail = client.get(f"/api/v1/orders/{order['orderId']}")

    assert {"orderId", "symbol", "side", "orderType", "timeInForce", "status",
            "price", "quantity", "currency", "orderedAt", "execution"} <= set(detail)
    assert {"filledQuantity", "averageFilledPrice", "filledAmount",
            "commission", "tax", "filledAt"} <= set(detail["execution"])


def test_numbers_are_strings_as_the_real_api_returns_them(dryrun):
    """Phase 4 parses these; a convenient float here would hide the mismatch."""
    client, _, _ = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })

    item = client.get("/api/v1/holdings")["items"][0]
    assert isinstance(item["quantity"], str)
    assert isinstance(item["lastPrice"], str)
    assert isinstance(
        client.get("/api/v1/buying-power", params={"currency": "KRW"})["cashBuyingPower"], str
    )


def test_us_symbols_are_priced_in_usd(dryrun):
    client, _, ledger = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "AAPL", "side": "BUY", "orderType": "MARKET", "quantity": "2"
    })

    assert ledger.cash("USD") == Decimal("10000") - Decimal("371")
    assert ledger.cash("KRW") == Decimal("10000000")


# ── Ledger persistence ───────────────────────────────────────────────────────


def test_the_ledger_survives_a_new_client(tmp_path):
    """The tracking agent runs as a separate process from the batch."""
    from trading.brokers.toss.dryrun import DryRunLedger, DryRunTossClient

    db = tmp_path / "dryrun.sqlite"
    first = DryRunTossClient(SpyRealClient(), ledger=DryRunLedger(db))
    first.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })

    second = DryRunTossClient(SpyRealClient(), ledger=DryRunLedger(db))
    assert second.get("/api/v1/holdings")["items"][0]["quantity"] == "10"


def test_reset_empties_the_simulated_account(tmp_path):
    from trading.brokers.toss.dryrun import DryRunLedger, DryRunTossClient

    ledger = DryRunLedger(tmp_path / "dryrun.sqlite")
    client = DryRunTossClient(SpyRealClient(), ledger=ledger)
    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "10"
    })

    ledger.reset()
    assert ledger.positions() == []
    assert client.get("/api/v1/orders") == []


def test_blocked_write_attempts_are_counted(dryrun):
    """Gives the operator a number to check after a demo run."""
    client, _, _ = dryrun

    client.post("/api/v1/orders", json_body={
        "symbol": "005930", "side": "BUY", "orderType": "MARKET", "quantity": "1"
    })
    assert client.order_calls_blocked == 1
