"""P0 money-path regressions (migration audit Phase 2).

Each test pins one of the live-money fixes: the configured buy amount actually
reaching the Toss order path, the corporate-status prefetch refusing quietly
instead of failing silently under Toss, and the session predicates the US
tracking loop asks its trader for.
"""

import asyncio
import datetime
from zoneinfo import ZoneInfo

import pytest

KST = ZoneInfo("Asia/Seoul")


def _stub_settings(**overrides):
    settings = {
        "default_unit_amount": 77000,
        "default_unit_amount_usd": 55,
        "auto_trading": True,
        "default_mode": "demo",
    }
    settings.update(overrides)
    return settings


# ── ① configured buy amount reaches the broker ──────────────────────────────


def test_the_broker_file_amount_reaches_the_toss_broker(monkeypatch):
    """default_unit_amount edited in toss_config.yaml must change order size.

    This is the fix for the dead-config defect: the factory used to consult
    only the env override, so the file value never left the YAML.
    """
    monkeypatch.setenv("TOSS_CLIENT_ID", "cid")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "sec")
    from trading.brokers import factory

    monkeypatch.setattr(factory.config, "trading_settings", _stub_settings)

    assert factory.build_toss_broker("KR", mode="demo").buy_amount == 77000
    assert factory.build_toss_broker("US", mode="demo").buy_amount == 55


def test_env_amount_still_beats_the_broker_file(monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "cid")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "sec")
    monkeypatch.setenv("PRISM_BUY_AMOUNT_KRW", "123000")
    from trading.brokers import factory

    monkeypatch.setattr(factory.config, "trading_settings", _stub_settings)

    assert factory.build_toss_broker("KR", mode="demo").buy_amount == 123000


# ── ② corporate-status prefetch refuses loudly under Toss ───────────────────


def test_corporate_status_skips_loudly_under_toss(monkeypatch, caplog):
    """The TIER0 forced-exit prefetch is KIS-only; under Toss it must say so
    once and return empty, not swallow a KIS import failure into silence."""
    monkeypatch.setenv("PRISM_BROKER", "toss")
    from cores.corporate_status import fetch_status_codes

    with caplog.at_level("WARNING"):
        result = asyncio.run(fetch_status_codes(["005930", "000660"]))

    assert result == {}
    assert any("KIS 전용" in record.message for record in caplog.records)


def test_corporate_status_survives_an_unrecognised_broker(monkeypatch, caplog):
    """never-raises contract: a PRISM_BROKER typo must skip loudly, not
    propagate BrokerConfigError or fall into the KIS-direct branch."""
    monkeypatch.setenv("PRISM_BROKER", "tos")
    from cores.corporate_status import fetch_status_codes

    with caplog.at_level("WARNING"):
        result = asyncio.run(fetch_status_codes(["005930"]))

    assert result == {}
    assert any("스킵" in record.message for record in caplog.records)


# ── ③ session predicates the US loop asks its trader for ────────────────────


def _kr_broker():
    from trading.brokers.toss.adapter import TossBroker

    return TossBroker(client=object(), market="KR")


def _us_broker():
    from trading.brokers.toss.adapter import TossBroker

    return TossBroker(client=object(), market="US")


def test_toss_kr_market_open_follows_the_canonical_window():
    broker = _kr_broker()
    regular = datetime.datetime(2026, 8, 18, 10, 0, tzinfo=KST)      # Tuesday
    closing = datetime.datetime(2026, 8, 18, 15, 45, tzinfo=KST)
    night = datetime.datetime(2026, 8, 18, 22, 0, tzinfo=KST)
    saturday = datetime.datetime(2026, 8, 22, 10, 0, tzinfo=KST)
    sunday = datetime.datetime(2026, 8, 23, 10, 0, tzinfo=KST)

    assert broker.is_market_open(now=regular) is True
    # Toss does not support closing-price orders, so 'closing' is not open here.
    assert broker.is_market_open(now=closing) is False
    assert broker.is_market_open(now=night) is False
    # The window function is clock-only; the weekday guard lives in the broker.
    assert broker.is_market_open(now=saturday) is False
    assert broker.is_market_open(now=sunday) is False


def test_toss_us_market_open_follows_the_session_calendar(monkeypatch):
    broker = _us_broker()

    monkeypatch.setattr(broker, "open_us_session", lambda *, now=None: "dayMarket")
    assert broker.is_market_open() is True

    monkeypatch.setattr(broker, "open_us_session", lambda *, now=None: None)
    assert broker.is_market_open() is False


def test_toss_reserved_orders_are_never_available():
    assert _kr_broker().is_reserved_order_available() is False
    assert _us_broker().is_reserved_order_available() is False


def test_kis_wrapper_answers_session_predicates_for_a_bare_domestic_trader():
    """The port now declares the predicates, so the KIS wrapper must answer
    even for the domestic trader, which has none of its own."""
    from trading.brokers.kis_adapter import KisBroker

    class Bare:
        pass

    broker = KisBroker(Bare(), market="KR")
    assert isinstance(broker.is_market_open(), bool)
    assert isinstance(broker.is_reserved_order_available(), bool)


def test_kis_wrapper_delegates_session_predicates_when_the_trader_has_them():
    from trading.brokers.kis_adapter import KisBroker

    class UsLike:
        def is_market_open(self):
            return True

        def is_reserved_order_available(self):
            return False

    broker = KisBroker(UsLike(), market="US")
    assert broker.is_market_open() is True
    assert broker.is_reserved_order_available() is False


# ── ④ zero sell quantity must refuse, not liquidate ─────────────────────────


def test_toss_sell_refuses_a_zero_quantity_instead_of_liquidating(monkeypatch):
    """quantity=0 is a caller's split arithmetic saying 'nothing for this row';
    the falsy fallback used to reinterpret it as 'sell everything held'."""
    from decimal import Decimal

    broker = _us_broker()
    monkeypatch.setattr(
        broker, "get_holding_quantity_checked", lambda s: ("HELD", Decimal("1"))
    )
    price_calls = []
    monkeypatch.setattr(
        broker, "get_current_price", lambda s: price_calls.append(s) or {"current_price": 100}
    )

    for zero in (0, Decimal("0"), Decimal("0.000000")):
        outcome = broker._sell("AAPL", None, zero)
        assert outcome["success"] is False
        assert "refusing full-liquidation" in outcome["message"]
    assert not price_calls  # refused before any further broker work


# ── ⑤ fractional quantities survive intent persistence ──────────────────────


def test_order_intent_stores_fractional_quantity_in_a_bindable_form(tmp_path):
    """sqlite3 cannot bind Decimal and json.dumps cannot serialize it; the
    intent layer used to die AFTER the holdings row was already deleted."""
    from decimal import Decimal

    from prism_core.order_intents import IntentStore, OrderIntent

    fractional = OrderIntent.create(
        market="US", account_id="acct", symbol="AAPL", side="SELL",
        order_style="market", source="test", source_position_id="p1",
        quantity=Decimal("0.84"),
    )
    assert fractional.quantity == "0.84"  # exact, bindable, JSON-safe

    whole = OrderIntent.create(
        market="US", account_id="acct", symbol="AAPL", side="SELL",
        order_style="market", source="test", source_position_id="p2",
        quantity=Decimal("3"),
    )
    assert whole.quantity == 3  # integral Decimals stay ints

    store = IntentStore(tmp_path / "intents.sqlite")
    reserved, info = store.reserve(fractional)
    assert reserved, info
