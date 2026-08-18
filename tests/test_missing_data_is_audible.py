"""Broken inputs must not sound like ordinary outcomes.

Every incident in this area had the same shape: the batch finished, the report
was produced, and the only trace was a line that read like a design choice.
"Top-down pool: empty (pure bottom-up mode)" is what a dead sector map looked
like for as long as it was dead. "Prefetched KR data: ['stock_ohlcv']" is what
a report with no investor flows looked like — a list of successes says nothing
about its own length.

The distinction these tests pin is between two things that used to log
identically:

  * no candidate qualified today — a real reading of the market, INFO
  * an input was missing, so nothing *could* qualify — broken, WARNING/ERROR

Levels are asserted, not just message text, because the batch runs at INFO and
a correct message at DEBUG is still silence.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest


# --- top-down selection ------------------------------------------------------


def _macro(**overrides):
    context = {
        "leading_sectors": [{"sector": "반도체와반도체장비", "confidence": 0.8}],
        "sector_map": {"005930": "반도체와반도체장비"},
        "market_regime": "moderate_bull",
    }
    context.update(overrides)
    return context


def test_an_empty_sector_map_is_reported_as_an_error(caplog):
    """It disables top-down selection outright rather than finding nothing."""
    import trigger_batch

    with caplog.at_level(logging.DEBUG):
        pool = trigger_batch._build_topdown_pool({}, _macro(sector_map={}), "Score")

    assert pool == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "an empty sector map logged nothing above ERROR"
    assert "sector map is empty" in errors[0].getMessage()


def test_missing_leading_sectors_is_a_warning_not_an_error(caplog):
    """The macro agent naming no leader can be a real reading of the market."""
    import trigger_batch

    with caplog.at_level(logging.DEBUG):
        trigger_batch._build_topdown_pool({}, _macro(leading_sectors=[]), "Score")

    levels = {r.levelno for r in caplog.records}
    assert logging.WARNING in levels
    assert logging.ERROR not in levels, "a legitimate market outcome escalated to ERROR"


def test_no_macro_context_at_all_is_a_warning(caplog):
    import trigger_batch

    with caplog.at_level(logging.DEBUG):
        assert trigger_batch._build_topdown_pool({}, None, "Score") == []

    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_the_sector_leader_trigger_says_when_it_is_disabled(caplog):
    """Without a sector map every ticker is skipped for want of a sector.

    The result is an empty frame either way, so the reason has to be said out
    loud or it is indistinguishable from a quiet day.
    """
    import trigger_batch

    empty = pd.DataFrame()
    with caplog.at_level(logging.DEBUG):
        result = trigger_batch.trigger_macro_sector_leader(
            "20260818", empty, empty, empty, _macro(sector_map={})
        )

    assert result.empty
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a disabled trigger logged nothing above ERROR"


def test_the_empty_pool_message_does_not_call_itself_a_mode(caplog):
    """"pure bottom-up mode" read as a deliberate choice. It was a fault."""
    import inspect

    import trigger_batch

    source = inspect.getsource(trigger_batch)

    assert "pure bottom-up mode" not in source


# --- prefetch ----------------------------------------------------------------


def test_an_incomplete_prefetch_names_what_is_missing(caplog, monkeypatch):
    """A list of successes reads as complete however short it is."""
    import cores.data_prefetch as dp

    monkeypatch.setattr(dp, "prefetch_stock_ohlcv", lambda *a: "ohlcv")
    monkeypatch.setattr(dp, "prefetch_stock_trading_volume", lambda *a: "")
    monkeypatch.setattr(dp, "prefetch_index_ohlcv", lambda *a: "index")

    with caplog.at_level(logging.DEBUG):
        result = dp.prefetch_kr_analysis_data("005930", "20260818", "20250818")

    assert "trading_volume" not in result
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a missing input logged nothing at WARNING"
    assert "trading_volume" in warnings[0].getMessage()


def test_a_complete_prefetch_stays_quiet(caplog, monkeypatch):
    """Warning on a healthy run would train the operator to ignore warnings."""
    import cores.data_prefetch as dp

    monkeypatch.setattr(dp, "prefetch_stock_ohlcv", lambda *a: "ohlcv")
    monkeypatch.setattr(dp, "prefetch_stock_trading_volume", lambda *a: "flows")
    monkeypatch.setattr(dp, "prefetch_index_ohlcv", lambda *a: "index")

    with caplog.at_level(logging.DEBUG):
        dp.prefetch_kr_analysis_data("005930", "20260818", "20250818")

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_total_prefetch_failure_says_so(caplog, monkeypatch):
    import cores.data_prefetch as dp

    monkeypatch.setattr(dp, "prefetch_stock_ohlcv", lambda *a: "")
    monkeypatch.setattr(dp, "prefetch_stock_trading_volume", lambda *a: "")
    monkeypatch.setattr(dp, "prefetch_index_ohlcv", lambda *a: "")

    with caplog.at_level(logging.DEBUG):
        assert dp.prefetch_kr_analysis_data("005930", "20260818", "20250818") == {}

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings
    assert "all 4 inputs" in warnings[0].getMessage()


@pytest.mark.parametrize(
    "level_name", ["stock_ohlcv", "trading_volume", "kospi_index", "kosdaq_index"]
)
def test_every_expected_input_is_named_when_it_alone_is_missing(
    caplog, monkeypatch, level_name
):
    """The expected-inputs list must not drift out of step with what is fetched."""
    import cores.data_prefetch as dp

    monkeypatch.setattr(
        dp, "prefetch_stock_ohlcv", lambda *a: "" if level_name == "stock_ohlcv" else "x"
    )
    monkeypatch.setattr(
        dp, "prefetch_stock_trading_volume",
        lambda *a: "" if level_name == "trading_volume" else "x",
    )

    def index(ticker, *a):
        blank = "1001" if level_name == "kospi_index" else "2001"
        return "" if ticker == blank and level_name.endswith("_index") else "x"

    monkeypatch.setattr(dp, "prefetch_index_ohlcv", index)

    with caplog.at_level(logging.DEBUG):
        dp.prefetch_kr_analysis_data("005930", "20260818", "20250818")

    messages = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert level_name in messages
