"""KIS adapter: proves the wrapper adds nothing.

The point of these tests is narrow and it is not "does KIS work" — it is
"does wrapping KIS change anything". So they assert on identity rather than
equality: a wrapper that rebuilds an equal dict has changed behaviour in the
way that matters here, because callers mutate and re-key those results.
"""

import asyncio

import pytest

from tests.test_broker_contract import FakeKisTrader, assert_satisfies_broker_port


class FakeUSTrader(FakeKisTrader):
    """US traders spell the reserved sell differently and omit the checked read."""

    # USStockTrading genuinely has no get_holding_quantity_checked.
    get_holding_quantity_checked = None

    def sell_reserved_order(self, *args, **kwargs):
        self.calls.append(("sell_reserved_order", args, kwargs))
        return self.sell_result


class FakeMultiAccountTrader(FakeKisTrader):
    """MultiAccountDomesticStockTrading exposes the same surface."""


def test_both_markets_satisfy_the_port():
    from trading.brokers.kis_adapter import KisBroker

    assert_satisfies_broker_port(KisBroker(FakeKisTrader(), market="KR"))
    assert_satisfies_broker_port(KisBroker(FakeUSTrader(), market="US"))


def test_multi_account_traders_wrap_identically():
    from trading.brokers.kis_adapter import KisBroker

    assert_satisfies_broker_port(KisBroker(FakeMultiAccountTrader(), market="KR"))


def test_market_is_normalised_and_validated():
    from trading.brokers.kis_adapter import KisBroker

    assert KisBroker(FakeKisTrader(), market="kr").market == "KR"
    assert KisBroker(FakeKisTrader(), market="us").market == "US"
    with pytest.raises(ValueError):
        KisBroker(FakeKisTrader(), market="JP")


def test_convenience_constructors_bind_the_market():
    from trading.brokers.kis_adapter import kis_domestic, kis_us

    assert kis_domestic(FakeKisTrader()).market == "KR"
    assert kis_us(FakeUSTrader()).market == "US"


# ── Delegation fidelity ──────────────────────────────────────────────────────


def test_async_order_arguments_reach_the_trader_unchanged():
    from trading.brokers.kis_adapter import KisBroker

    trader = FakeKisTrader()
    broker = KisBroker(trader, market="KR")

    asyncio.run(broker.async_buy_stock("005930", 100000, limit_price=81000))
    asyncio.run(broker.async_sell_stock("005930", quantity=3, timeout=15.0))

    assert trader.calls == [
        ("async_buy_stock", ("005930", 100000), {"limit_price": 81000}),
        ("async_sell_stock", ("005930",), {"quantity": 3, "timeout": 15.0}),
    ]


def test_sync_order_arguments_reach_the_trader_unchanged():
    from trading.brokers.kis_adapter import KisBroker

    trader = FakeKisTrader()
    broker = KisBroker(trader, market="KR")

    broker.amend_order("005930", "0000117057", 81000, quantity=3)
    broker.cancel_order("005930", "0000117057", quantity=3)
    broker.buy_reserved_order("005930", buy_amount=100000, limit_price=81000)

    assert trader.calls == [
        ("amend_order", ("005930", "0000117057", 81000), {"quantity": 3}),
        ("cancel_order", ("005930", "0000117057"), {"quantity": 3}),
        ("buy_reserved_order", ("005930",), {"buy_amount": 100000, "limit_price": 81000}),
    ]


def test_query_arguments_reach_the_trader_unchanged():
    from trading.brokers.kis_adapter import KisBroker

    trader = FakeKisTrader()
    broker = KisBroker(trader, market="KR")

    broker.get_current_price("005930")
    broker.get_portfolio()
    broker.get_account_summary()
    broker.get_holding_quantity("005930")
    broker.get_holding_quantity_checked("005930")
    broker.calculate_buy_quantity("005930", buy_amount=100000)

    assert trader.calls == [
        ("get_current_price", ("005930",), {}),
        ("get_portfolio", (), {}),
        ("get_account_summary", (), {}),
        ("get_holding_quantity", ("005930",), {}),
        ("get_holding_quantity_checked", ("005930",), {}),
        ("calculate_buy_quantity", ("005930",), {"buy_amount": 100000}),
    ]


def test_results_are_returned_by_identity_not_rebuilt():
    """A copied dict is a behaviour change: callers mutate what they get back."""
    from trading.brokers.kis_adapter import KisBroker

    trader = FakeKisTrader()
    broker = KisBroker(trader, market="KR")

    assert asyncio.run(broker.async_buy_stock("005930")) is trader.buy_result
    assert asyncio.run(broker.async_sell_stock("005930")) is trader.sell_result
    assert broker.amend_order("005930") is trader.buy_result
    assert broker.cancel_order("005930") is trader.buy_result
    assert broker.buy_reserved_order("005930") is trader.buy_result


def test_outcome_unknown_is_preserved():
    """Losing this flag would let an ambiguous order be recorded as rejected."""
    from trading.brokers.kis_adapter import KisBroker

    trader = FakeKisTrader()
    trader.buy_result = {
        "success": False,
        "outcome_unknown": True,
        "stock_code": "005930",
        "current_price": 0,
        "quantity": 0,
        "total_amount": 0,
        "order_no": None,
        "message": "Buy request timeout (30.0s)",
        "timestamp": "2026-08-17T09:00:00+09:00",
    }
    broker = KisBroker(trader, market="KR")

    result = asyncio.run(broker.async_buy_stock("005930"))
    assert result["outcome_unknown"] is True
    assert result["success"] is False


def test_empty_account_summary_stays_empty_dict_not_none():
    """get_account_summary returns {} on failure; None would break callers."""
    from trading.brokers.kis_adapter import KisBroker

    broker = KisBroker(FakeKisTrader(), market="KR")
    assert broker.get_account_summary() == {}
    assert broker.get_account_summary() is not None


def test_reserved_sell_resolves_each_trader_spelling():
    """Domestic says sell_all_reserved_order; US says sell_reserved_order."""
    from trading.brokers.kis_adapter import KisBroker

    domestic = FakeKisTrader()
    KisBroker(domestic, market="KR").sell_reserved_order("005930", limit_price=81000)
    assert domestic.calls == [
        ("sell_all_reserved_order", ("005930",), {"limit_price": 81000})
    ]

    us = FakeUSTrader()
    KisBroker(us, market="US").sell_reserved_order("AAPL", limit_price=185.5)
    assert us.calls == [("sell_reserved_order", ("AAPL",), {"limit_price": 185.5})]


def test_the_wrapped_trader_stays_reachable():
    from trading.brokers.kis_adapter import KisBroker

    trader = FakeKisTrader()
    assert KisBroker(trader, market="KR").trader is trader


def test_importing_the_adapter_does_not_pull_in_kis_auth():
    """Importing the contract must not read credential files."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import trading.brokers.kis_adapter as m, sys; "
            "print('kis_auth' in sys.modules)",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", "adapter import pulled in kis_auth"
