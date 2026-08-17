"""The contract every broker adapter must satisfy.

`assert_satisfies_broker_port` is the reusable part: Phase 4's Toss adapter is
expected to import and pass this same function. Keeping the checks here rather
than in each adapter's own test file is what stops the contract from quietly
becoming "whatever KIS happens to do".

`runtime_checkable` only proves the methods exist, so these checks call them and
inspect what comes back. A protocol that is satisfied structurally but violated
semantically is the failure mode worth guarding.
"""

import asyncio

import pytest


ORDER_METHODS = (
    "async_buy_stock",
    "async_sell_stock",
    "amend_order",
    "cancel_order",
    "buy_reserved_order",
    "sell_reserved_order",
)

QUERY_METHODS = (
    "get_current_price",
    "get_portfolio",
    "get_account_summary",
    "get_holding_quantity",
    "get_holding_quantity_checked",
    "calculate_buy_quantity",
)

REQUIRED_OUTCOME_KEYS = frozenset(
    {
        "success",
        "stock_code",
        "current_price",
        "quantity",
        "total_amount",
        "order_no",
        "message",
        "timestamp",
    }
)


def assert_satisfies_broker_port(broker):
    """Assert `broker` honours the port. Reusable across adapters.

    Deliberately not named `test_*` — pytest would collect it and call it with
    no argument.
    """
    from trading.brokers.base import BrokerPort

    assert isinstance(broker, BrokerPort), (
        f"{type(broker).__name__} does not structurally satisfy BrokerPort"
    )

    assert isinstance(broker.name, str) and broker.name, "name must be a non-empty string"
    assert broker.name == broker.name.lower(), "name must be lowercase"
    assert broker.market in {"KR", "US"}, f"market must be KR or US, got {broker.market!r}"

    for method_name in ORDER_METHODS + QUERY_METHODS:
        assert callable(getattr(broker, method_name, None)), (
            f"{method_name} missing or not callable"
        )


def assert_order_outcome_shape(result):
    """Assert an order result carries the keys `ExecutionService` reads."""
    assert isinstance(result, dict), f"order result must be a dict, got {type(result).__name__}"
    missing = REQUIRED_OUTCOME_KEYS - set(result)
    assert not missing, f"order result missing required keys: {sorted(missing)}"


def assert_holding_state_survives_normalisation(state, expected):
    """Assert a three-state holding answer is not collapsed to UNKNOWN.

    Uses the production normaliser as the judge rather than reimplementing its
    rules, so the contract cannot drift away from what callers actually enforce.
    """
    from prism_core.execution_service import normalize_checked_holding

    assert normalize_checked_holding(state) == expected


# ── A trader that behaves, for exercising the contract itself ────────────────


class FakeKisTrader:
    """Duck-typed stand-in for DomesticStockTrading."""

    def __init__(self, holding_state=("FLAT", 0)):
        self.calls = []
        self.holding_state = holding_state
        self.buy_result = {
            "success": True,
            "stock_code": "005930",
            "current_price": 70000,
            "quantity": 10,
            "total_amount": 700000,
            "order_no": "0000117057",
            "message": "Buy completed",
            "timestamp": "2026-08-17T09:00:00+09:00",
        }
        self.sell_result = dict(self.buy_result, message="Sell completed")

    async def async_buy_stock(self, *args, **kwargs):
        self.calls.append(("async_buy_stock", args, kwargs))
        return self.buy_result

    async def async_sell_stock(self, *args, **kwargs):
        self.calls.append(("async_sell_stock", args, kwargs))
        return self.sell_result

    def amend_order(self, *args, **kwargs):
        self.calls.append(("amend_order", args, kwargs))
        return self.buy_result

    def cancel_order(self, *args, **kwargs):
        self.calls.append(("cancel_order", args, kwargs))
        return self.buy_result

    def buy_reserved_order(self, *args, **kwargs):
        self.calls.append(("buy_reserved_order", args, kwargs))
        return self.buy_result

    def sell_all_reserved_order(self, *args, **kwargs):
        self.calls.append(("sell_all_reserved_order", args, kwargs))
        return self.sell_result

    def get_current_price(self, *args, **kwargs):
        self.calls.append(("get_current_price", args, kwargs))
        return {"current_price": 70000}

    def get_portfolio(self):
        self.calls.append(("get_portfolio", (), {}))
        return []

    def get_account_summary(self):
        self.calls.append(("get_account_summary", (), {}))
        return {}

    def get_holding_quantity(self, *args, **kwargs):
        self.calls.append(("get_holding_quantity", args, kwargs))
        return 0

    def get_holding_quantity_checked(self, *args, **kwargs):
        self.calls.append(("get_holding_quantity_checked", args, kwargs))
        return self.holding_state

    def calculate_buy_quantity(self, *args, **kwargs):
        self.calls.append(("calculate_buy_quantity", args, kwargs))
        return 10


# ── Contract-of-the-contract tests ───────────────────────────────────────────


def test_a_conforming_adapter_satisfies_the_port():
    from trading.brokers.kis_adapter import KisBroker

    assert_satisfies_broker_port(KisBroker(FakeKisTrader(), market="KR"))


def test_order_results_carry_the_keys_execution_service_reads():
    from trading.brokers.kis_adapter import KisBroker

    broker = KisBroker(FakeKisTrader(), market="KR")
    assert_order_outcome_shape(asyncio.run(broker.async_buy_stock("005930")))
    assert_order_outcome_shape(asyncio.run(broker.async_sell_stock("005930")))


@pytest.mark.parametrize(
    "state, expected",
    [
        (("HELD", 5), ("HELD", 5)),
        (("FLAT", 0), ("FLAT", 0)),
        (("UNKNOWN", None), ("UNKNOWN", None)),
        # A malformed answer must degrade to UNKNOWN, never to a sellable zero.
        (("HELD", None), ("UNKNOWN", None)),
        (("FLAT", 3), ("UNKNOWN", None)),
        (("HELD", "5"), ("UNKNOWN", None)),
    ],
)
def test_holding_states_pass_through_the_production_normaliser(state, expected):
    from trading.brokers.kis_adapter import KisBroker

    broker = KisBroker(FakeKisTrader(holding_state=state), market="KR")
    assert_holding_state_survives_normalisation(
        broker.get_holding_quantity_checked("005930"), expected
    )


def test_a_missing_capability_raises_unsupported_rather_than_failing_quietly():
    """The distinction Toss will depend on: absent is not the same as failed."""
    from trading.brokers.base import BrokerUnsupported
    from trading.brokers.kis_adapter import KisBroker

    trader = FakeKisTrader()
    trader.get_holding_quantity_checked = None

    broker = KisBroker(trader, market="US")
    with pytest.raises(BrokerUnsupported):
        broker.get_holding_quantity_checked("AAPL")


def test_unsupported_and_unavailable_are_separate_types():
    """Collapsing these would let a permanent gap be retried forever."""
    from trading.brokers.base import BrokerError, BrokerUnavailable, BrokerUnsupported

    assert issubclass(BrokerUnsupported, BrokerError)
    assert issubclass(BrokerUnavailable, BrokerError)
    assert not issubclass(BrokerUnsupported, BrokerUnavailable)
    assert not issubclass(BrokerUnavailable, BrokerUnsupported)
