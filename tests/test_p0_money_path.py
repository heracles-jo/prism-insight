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


# ── ③ session predicates the US loop asks its trader for ────────────────────


def _kr_broker():
    from trading.brokers.toss.adapter import TossBroker

    return TossBroker(client=object(), market="KR")


def _us_broker():
    from trading.brokers.toss.adapter import TossBroker

    return TossBroker(client=object(), market="US")


def test_toss_kr_market_open_follows_the_canonical_window():
    broker = _kr_broker()
    regular = datetime.datetime(2026, 8, 18, 10, 0, tzinfo=KST)
    closing = datetime.datetime(2026, 8, 18, 15, 45, tzinfo=KST)
    night = datetime.datetime(2026, 8, 18, 22, 0, tzinfo=KST)

    assert broker.is_market_open(now=regular) is True
    # Toss does not support closing-price orders, so 'closing' is not open here.
    assert broker.is_market_open(now=closing) is False
    assert broker.is_market_open(now=night) is False


def test_toss_us_market_open_follows_the_session_calendar(monkeypatch):
    broker = _us_broker()

    monkeypatch.setattr(broker, "open_us_session", lambda *, now=None: "dayMarket")
    assert broker.is_market_open() is True

    monkeypatch.setattr(broker, "open_us_session", lambda *, now=None: None)
    assert broker.is_market_open() is False


def test_toss_reserved_orders_are_never_available():
    assert _kr_broker().is_reserved_order_available() is False
    assert _us_broker().is_reserved_order_available() is False
