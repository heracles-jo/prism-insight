"""The price lookup has to work on an install without KIS or KRX.

`_get_price_from_kis` imported the KIS trading module directly, and its
docstring assumed "KIS credentials are already configured wherever the tracking
agents run". A Toss install has none: the call 403s, and because a fresh buy
candidate has no `stock_holdings` row the DB fallback returns 0, so the
candidate is dropped before anything looks at it. One batch reported
`Purchased: 0 items` with all three candidates lost exactly there.

The order is KRX, then the broker, then the source chain, then the DB. Each of
the middle two exists because the one before it can be unavailable on a real
install, so both are pinned here.
"""

from __future__ import annotations

import asyncio

import pytest

import tracking.helpers as helpers


class FakeTrader:
    def __init__(self, price):
        self._price = price
        self.calls: list[str] = []

    def get_current_price(self, ticker):
        self.calls.append(ticker)
        return {"current_price": self._price} if self._price else None


# --- broker step -------------------------------------------------------------


def test_the_broker_is_asked_rather_than_kis_directly(monkeypatch):
    """The factory reads PRISM_BROKER; importing the KIS module does not."""
    trader = FakeTrader(91800)
    monkeypatch.setattr("trading.brokers.factory.domestic_trader", lambda **kw: trader)

    price = asyncio.run(helpers._get_price_from_broker("042660"))

    assert price == 91800.0
    assert trader.calls == ["042660"]


def test_the_trader_is_used_directly_not_as_a_context(monkeypatch):
    """`domestic_trader()` returns a trader, unlike the context it replaced.

    Carrying the old `async with` across would raise on an object that is not a
    context manager — and the except clause would swallow it as a price of 0.
    """
    trader = FakeTrader(1000)
    monkeypatch.setattr("trading.brokers.factory.domestic_trader", lambda **kw: trader)

    assert asyncio.run(helpers._get_price_from_broker("005930")) == 1000.0


def test_a_broker_failure_degrades_to_zero_rather_than_raising(monkeypatch):
    """The caller reads 0 as "try the next source"; an exception would end the run."""

    def boom(**kwargs):
        raise RuntimeError("403")

    monkeypatch.setattr("trading.brokers.factory.domestic_trader", boom)

    assert asyncio.run(helpers._get_price_from_broker("042660")) == 0.0


def test_a_broker_with_no_quote_returns_zero(monkeypatch):
    monkeypatch.setattr(
        "trading.brokers.factory.domestic_trader", lambda **kw: FakeTrader(None)
    )

    assert asyncio.run(helpers._get_price_from_broker("042660")) == 0.0


# --- source chain step -------------------------------------------------------


def test_the_chain_supplies_a_last_close(monkeypatch):
    import pandas as pd

    frame = pd.DataFrame({"Close": [91700.0, 91800.0]})
    monkeypatch.setattr(
        "cores.market_data.get_market_ohlcv_by_date", lambda *a, **k: frame
    )

    assert helpers._get_price_from_chain("042660") == 91800.0


def test_an_empty_chain_result_is_zero_not_an_exception(monkeypatch):
    """The chain returns an empty frame when nothing answers, rather than raising."""
    import pandas as pd

    monkeypatch.setattr(
        "cores.market_data.get_market_ohlcv_by_date", lambda *a, **k: pd.DataFrame()
    )

    assert helpers._get_price_from_chain("042660") == 0.0


def test_a_frame_without_a_close_column_is_zero(monkeypatch):
    import pandas as pd

    monkeypatch.setattr(
        "cores.market_data.get_market_ohlcv_by_date",
        lambda *a, **k: pd.DataFrame({"Open": [1.0]}),
    )

    assert helpers._get_price_from_chain("042660") == 0.0


def test_the_chain_is_asked_with_start_end_ticker(monkeypatch):
    """Argument order is (start, end, ticker), and is easy to get backwards."""
    seen: dict[str, str] = {}

    def capture(start, end, ticker):
        import pandas as pd

        seen.update(start=start, end=end, ticker=ticker)
        return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setattr("cores.market_data.get_market_ohlcv_by_date", capture)

    helpers._get_price_from_chain("042660")

    assert seen["ticker"] == "042660"
    assert len(seen["start"]) == 8 and len(seen["end"]) == 8
    assert seen["start"] < seen["end"]


# --- the module itself -------------------------------------------------------


def test_no_module_scope_import_of_the_kis_trader():
    """The repo tripwire forbids it, and this file is where one used to live.

    Pinned locally as well as globally: if the tripwire's allowlist ever grows,
    this module in particular must stay out of it.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(helpers.__file__).read_text(encoding="utf-8"))
    module_scope_imports: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_scope_imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module_scope_imports.append(node.module or "")

    assert not [name for name in module_scope_imports if "domestic_stock_trading" in name]
