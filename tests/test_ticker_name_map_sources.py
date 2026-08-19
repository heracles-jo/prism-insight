"""Where the batch gets its ticker names, and what it costs when KRX is down.

Found by running trigger_batch.py against a live KRX account whose session
kept being invalidated: the batch hung in the client's login retry loop —
a real browser login, 20-30s of backoff, twice — on the critical path of the
morning run, only to end up with the empty map it already tolerated.

update_stock_data.py writes the same code -> name map to stock_map.json at
07:00 every day, and the Telegram and Kakao bots already read it. The batch
did not. Reading it first took the run from minutes to seconds.
"""

import json
from datetime import datetime, timedelta

import pytest

import trigger_batch as tb


@pytest.fixture(autouse=True)
def _clear_cache():
    tb._TICKER_NAME_CACHE = None
    yield
    tb._TICKER_NAME_CACHE = None


def _write_map(tmp_path, monkeypatch, *, entries, age_days=0):
    payload = {
        "code_to_name": entries,
        "updated_at": (datetime.now() - timedelta(days=age_days)).isoformat(),
    }
    path = tmp_path / "stock_map.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(tb, "__file__", str(tmp_path / "trigger_batch.py"))
    return path


def test_the_daily_map_file_is_used_before_any_network_call(tmp_path, monkeypatch):
    _write_map(tmp_path, monkeypatch, entries={"005930": "삼성전자"})

    def _must_not_be_called():
        raise AssertionError("KRX was contacted despite a usable stock_map.json")

    monkeypatch.setattr(tb, "_get_client", _must_not_be_called)

    assert tb._get_ticker_name_map() == {"005930": "삼성전자"}


def test_a_stale_map_is_refused_so_new_listings_are_not_missed(tmp_path, monkeypatch):
    _write_map(tmp_path, monkeypatch, entries={"005930": "삼성전자"}, age_days=30)

    assert tb._load_stock_map_file() is None


def test_a_missing_or_corrupt_map_is_just_a_cache_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(tb, "__file__", str(tmp_path / "trigger_batch.py"))
    assert tb._load_stock_map_file() is None

    (tmp_path / "stock_map.json").write_text("{not json", encoding="utf-8")
    assert tb._load_stock_map_file() is None


def test_krx_failure_falls_through_to_finance_data_reader(tmp_path, monkeypatch):
    """The batch must not degrade every name to a bare ticker code just because
    KRX will not log in — that reached users as "009150 (009150)" in 2026-08."""
    import pandas as pd

    monkeypatch.setattr(tb, "__file__", str(tmp_path / "trigger_batch.py"))  # no map file

    def _krx_down():
        raise RuntimeError("session invalidated")

    monkeypatch.setattr(tb, "_get_client", _krx_down)

    class _FDR:
        @staticmethod
        def StockListing(_market):
            return pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"]})

    monkeypatch.setitem(__import__("sys").modules, "FinanceDataReader", _FDR)

    assert tb._get_ticker_name_map() == {"005930": "삼성전자"}
