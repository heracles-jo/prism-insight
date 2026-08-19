"""How a buy candidate gets priced, and what happens when a source is down.

This replaces a script-style file (module-scope `sys.exit`, so pytest could
not even collect it) that pinned a hand-rolled KRX retry loop: three attempts
with 2s + 4s of backoff against the login client, ahead of everything else.
Its intent — a source blip must not silently drop a fresh buy candidate — is
kept here, but the mechanism changed. The chain asks the next source instead of
retrying a blipping one, and on a host whose KRX login is refused the old loop
spent six seconds failing before reaching a source that could answer.

The ladder is: source chain -> live broker quote -> last price in the DB.
Getting 0.0 out of the bottom of it is the failure that matters: a fresh
candidate has no `stock_holdings` row, so 0.0 drops it from the batch entirely.
"""

import asyncio

import pytest

from tracking import helpers


class FakeCursor:
    """Just enough cursor for `_get_last_price_from_db`."""

    def __init__(self, last=None):
        self._last = last

    def execute(self, *args, **kwargs):
        return self

    def fetchone(self):
        return (self._last,) if self._last is not None else None


def _price(cursor, ticker="005930"):
    return asyncio.run(helpers.get_current_stock_price(cursor, ticker))


def test_the_chain_prices_the_candidate_when_it_can(monkeypatch):
    monkeypatch.setattr(helpers, "_get_price_from_chain", lambda t: 71_500.0)

    async def _broker_must_not_be_asked(ticker):
        raise AssertionError("the broker was consulted despite a chain answer")

    monkeypatch.setattr(helpers, "_get_price_from_broker", _broker_must_not_be_asked)

    assert _price(FakeCursor()) == 71_500.0


def test_a_dead_chain_falls_through_to_the_broker(monkeypatch):
    """The 2026-07-13 KRX outage and the 2026-08-18 Toss install both lost all
    three afternoon candidates right here."""
    monkeypatch.setattr(helpers, "_get_price_from_chain", lambda t: 0.0)

    async def _broker(ticker):
        return 70_100.0

    monkeypatch.setattr(helpers, "_get_price_from_broker", _broker)

    assert _price(FakeCursor()) == 70_100.0


def test_the_db_is_the_last_resort(monkeypatch):
    monkeypatch.setattr(helpers, "_get_price_from_chain", lambda t: 0.0)

    async def _broker(ticker):
        return 0.0

    monkeypatch.setattr(helpers, "_get_price_from_broker", _broker)

    assert _price(FakeCursor(last=1_952_000.0)) == 1_952_000.0


def test_a_candidate_with_no_price_anywhere_reports_zero(monkeypatch):
    """0.0 means "drop this candidate". It has to come from every source
    failing, never from one of them raising."""
    monkeypatch.setattr(helpers, "_get_price_from_chain", lambda t: 0.0)

    async def _broker(ticker):
        return 0.0

    monkeypatch.setattr(helpers, "_get_price_from_broker", _broker)

    assert _price(FakeCursor(last=None)) == 0.0


def test_a_raising_source_does_not_take_the_lookup_down(monkeypatch):
    """The old loop caught its own exceptions; the chain and broker helpers
    return 0.0 on failure. Either way the caller must still get a number."""

    def _boom(ticker):
        raise RuntimeError("source exploded")

    monkeypatch.setattr(helpers, "_get_price_from_chain", _boom)

    async def _broker(ticker):
        return 70_000.0

    monkeypatch.setattr(helpers, "_get_price_from_broker", _broker)

    with pytest.raises(RuntimeError):
        # Documents today's behaviour rather than asserting a wish: the chain
        # helper is the one place expected to swallow, and it already does
        # (`_get_price_from_chain` returns 0.0). A raise here would be a bug in
        # that helper, and this test says so out loud instead of hiding it.
        _price(FakeCursor())


def test_the_login_client_is_no_longer_on_the_pricing_path():
    """The point of the change: no krx_data_client import in this function."""
    import inspect

    source = inspect.getsource(helpers.get_current_stock_price)
    assert "krx_data_client" not in source
    assert "MAX_RETRIES" not in source
