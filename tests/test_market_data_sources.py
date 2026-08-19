"""Falling back between market data providers.

The behaviour under test is the one that failed in production on 2026-08-04:
KRX restricted the server's IP, `cores/stock_chart.py` returned `None` from
every call, and the afternoon report shipped at 17,387 characters instead of
351,311 with no error logged anywhere. Screening survived because it already
had a second source; the report path did not.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from cores.market_data import (
    default_chain,
    get_market_ohlcv_by_date,
    get_market_trading_volume_by_date,
    get_market_trading_volume_by_investor,
    set_default_chain,
)
from cores.market_data.schema import has_ohlcv, normalize, to_dashed
from cores.market_data.source import SourceChain, Unavailable, Unsupported

DATES = pd.date_range("2026-08-01", periods=3)


def ohlcv(close: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [close] * 3,
            "High": [close] * 3,
            "Low": [close] * 3,
            "Close": [close] * 3,
            "Volume": [1_000] * 3,
        },
        index=DATES,
    )


class FakeSource:
    """A source scripted to answer, refuse, or fail."""

    def __init__(self, name: str, *, result=None, raises=None) -> None:
        self.name = name
        self._result = result
        self._raises = raises
        self.calls: list[tuple] = []

    def price_history(self, ticker, start, end, *, adjusted=True):
        self.calls.append(("price_history", ticker))
        if self._raises:
            raise self._raises
        return self._result

    def investor_flows(self, ticker, start, end):
        self.calls.append(("investor_flows", ticker))
        if self._raises:
            raise self._raises
        return self._result

    def ticker_name(self, ticker):
        self.calls.append(("ticker_name", ticker))
        if self._raises:
            raise self._raises
        return self._result


@pytest.fixture(autouse=True)
def _restore_chain():
    yield
    set_default_chain(None)


class TestSchema:
    def test_korean_headers_become_english(self):
        frame = normalize(
            pd.DataFrame(
                {"시가": [1], "고가": [2], "저가": [3], "종가": [4], "거래량": [5]},
                index=["20260801"],
            )
        )

        assert set(frame.columns) == {"Open", "High", "Low", "Close", "Volume"}

    def test_the_index_becomes_dates_and_sorts_oldest_first(self):
        frame = normalize(
            pd.DataFrame({"Close": [2, 1]}, index=["20260802", "20260801"])
        )

        assert isinstance(frame.index, pd.DatetimeIndex)
        assert list(frame["Close"]) == [1, 2]

    def test_rows_without_a_close_are_not_a_price_series(self):
        # A frame that is merely non-empty renders as a blank chart rather than
        # an error, which is how the outage stayed invisible.
        assert not has_ohlcv(pd.DataFrame({"Foo": [1]}, index=DATES[:1]))
        assert has_ohlcv(ohlcv())

    def test_empty_is_not_a_price_series(self):
        assert not has_ohlcv(pd.DataFrame())

    def test_dates_convert_to_the_dashed_form_providers_want(self):
        assert to_dashed("20260804") == "2026-08-04"


class TestChainOrder:
    def test_the_first_source_that_answers_wins(self):
        first = FakeSource("first", result=ohlcv(100))
        second = FakeSource("second", result=ohlcv(200))

        frame = SourceChain([first, second]).fetch("price_history", "005930", "a", "b")

        assert frame["Close"].iloc[0] == 100
        assert second.calls == []

    def test_an_unavailable_source_hands_over(self):
        down = FakeSource("down", raises=Unavailable("IP restricted"))
        up = FakeSource("up", result=ohlcv(200))

        frame = SourceChain([down, up]).fetch("price_history", "005930", "a", "b")

        assert frame["Close"].iloc[0] == 200
        assert up.calls == [("price_history", "005930")]

    def test_a_source_that_lacks_the_capability_is_skipped(self):
        # Not a failure: FinanceDataReader simply has no investor flows.
        without = FakeSource("without", raises=Unsupported("no flows here"))
        with_it = FakeSource("with", result=ohlcv())

        frame = SourceChain([without, with_it]).fetch(
            "investor_flows", "005930", "a", "b"
        )

        assert not frame.empty

    def test_an_unexpected_error_does_not_stop_the_chain(self):
        # A provider bug must not take the report down with it.
        broken = FakeSource("broken", raises=RuntimeError("boom"))
        healthy = FakeSource("healthy", result=ohlcv(300))

        frame = SourceChain([broken, healthy]).fetch("price_history", "005930", "a", "b")

        assert frame["Close"].iloc[0] == 300

    def test_exhausting_every_source_raises_rather_than_returning_empty(self):
        chain = SourceChain(
            [
                FakeSource("a", raises=Unavailable("restricted")),
                FakeSource("b", raises=Unsupported("nope")),
            ]
        )

        with pytest.raises(Unavailable) as excinfo:
            chain.fetch("price_history", "005930", "a", "b")

        # The message has to name every attempt; "not found" is what made the
        # real outage take two hours to diagnose.
        assert "restricted" in str(excinfo.value)
        assert "unsupported" in str(excinfo.value)

    def test_a_chain_needs_at_least_one_source(self):
        with pytest.raises(ValueError):
            SourceChain([])

    def test_a_missing_capability_is_skipped_not_crashed(self):
        class Partial:
            name = "partial"

        chain = SourceChain([Partial(), FakeSource("full", result=ohlcv())])

        assert not chain.fetch("price_history", "005930", "a", "b").empty


class TestConfiguredOrder:
    def test_the_order_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "fdr,krx")
        set_default_chain(None)

        assert default_chain().names == ["fdr", "krx"]

    def test_a_single_source_is_allowed(self, monkeypatch):
        # A host that cannot reach KRX at all should not need a code change.
        monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "fdr")
        set_default_chain(None)

        assert default_chain().names == ["fdr"]

    def test_naver_can_be_configured_as_an_investor_flow_fallback(self, monkeypatch):
        monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "fdr,naver,krx")
        set_default_chain(None)

        assert default_chain().names == ["fdr", "naver", "krx"]

    def test_an_unknown_name_is_ignored_rather_than_fatal(self, monkeypatch):
        monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", "nonsense,fdr")
        set_default_chain(None)

        assert default_chain().names == ["fdr"]

    def test_the_default_is_krx_first(self, monkeypatch):
        monkeypatch.delenv("PRISM_MARKET_DATA_SOURCES", raising=False)
        set_default_chain(None)

        assert default_chain().names == ["krx", "fdr"]


class TestCallerFacingApi:
    def test_callers_still_receive_a_frame(self):
        set_default_chain(SourceChain([FakeSource("only", result=ohlcv(150))]))

        frame = get_market_ohlcv_by_date("20260801", "20260803", "005930")

        assert frame["Close"].iloc[0] == 150

    def test_total_exhaustion_reaches_the_caller_as_empty(self):
        # stock_chart reads an empty frame as "skip this chart"; that is the
        # right end state once every source has been asked, and it is logged.
        set_default_chain(
            SourceChain([FakeSource("down", raises=Unavailable("restricted"))])
        )

        frame = get_market_ohlcv_by_date("20260801", "20260803", "005930")

        assert frame.empty

    def test_intraday_estimate_is_appended_to_daily_history(self, monkeypatch):
        history = pd.DataFrame(
            {
                "외국인합계": [10],
                "기관합계": [20],
                "개인": [-35],
                "기타합계": [5],
            },
            index=pd.to_datetime(["2026-08-06"]),
        )
        estimate = pd.DataFrame(
            {"외국인합계": [100], "기관합계": [200], "개인·기타합계": [-300]},
            index=pd.to_datetime(["2026-08-07"]),
        )
        estimate.attrs.update(
            intraday_estimate=True,
            estimate_as_of="2026-08-07 14:30 KST",
            estimate_note="오늘 값은 KIS 장중 추정치입니다.",
        )

        class HybridSource(FakeSource):
            def investor_flows(self, ticker, start, end):
                self.calls.append(("investor_flows", ticker, start, end))
                return history

            def intraday_investor_estimate(self, ticker, *, as_of=None):
                self.calls.append(("intraday_investor_estimate", ticker, as_of))
                return estimate

        source = HybridSource("kis")
        set_default_chain(SourceChain([source]))
        monkeypatch.setattr(
            "cores.market_data._now_kst",
            lambda: pd.Timestamp("2026-08-07 14:52", tz="Asia/Seoul").to_pydatetime(),
        )

        frame = get_market_trading_volume_by_date("20260801", "20260807", "005930")

        assert list(frame.index.strftime("%Y%m%d")) == ["20260806", "20260807"]
        assert frame.loc["2026-08-07", "외국인합계"] == 100
        assert pd.isna(frame.loc["2026-08-07", "개인"])
        assert frame.loc["2026-08-06", "개인·기타합계"] == -30
        assert frame.loc["2026-08-07", "개인·기타합계"] == -300
        assert frame.attrs["intraday_estimate"] is True
        assert source.calls[0][3] == "20260806"

    def test_after_1540_uses_daily_flow_without_estimate(self, monkeypatch):
        source = FakeSource("kis", result=ohlcv(150))
        set_default_chain(SourceChain([source]))
        monkeypatch.setattr(
            "cores.market_data._now_kst",
            lambda: pd.Timestamp("2026-08-07 15:40", tz="Asia/Seoul").to_pydatetime(),
        )

        get_market_trading_volume_by_date("20260801", "20260807", "005930")

        assert source.calls == [("investor_flows", "005930")]

    def test_investor_compatibility_api_no_longer_calls_krx_directly(self, monkeypatch):
        source = FakeSource("chain", result=ohlcv(175))
        set_default_chain(SourceChain([source]))
        monkeypatch.setattr(
            "cores.market_data._now_kst",
            lambda: pd.Timestamp("2026-08-07 16:00", tz="Asia/Seoul").to_pydatetime(),
        )

        frame = get_market_trading_volume_by_investor(
            "20260801", "20260807", "005930"
        )

        assert frame["Close"].iloc[0] == 175
        assert source.calls == [("investor_flows", "005930")]


class TestRealSources:
    """Shape of the real adapters, without calling out to the network."""

    def test_fdr_declares_what_it_cannot_do(self):
        from cores.market_data.fdr_source import FdrSource

        source = FdrSource()
        with pytest.raises(Unsupported):
            source.investor_flows("005930", "20260801", "20260803")
        with pytest.raises(Unsupported):
            source.fundamentals("005930", "20260801", "20260803")

    def test_fdr_refuses_an_index_it_has_no_mapping_for(self):
        from cores.market_data.fdr_source import FdrSource

        with pytest.raises(Unsupported):
            FdrSource().index_history("9999", "20260801", "20260803")

    def test_fdr_maps_the_indices_the_reports_chart(self):
        from cores.market_data.fdr_source import INDEX_SYMBOLS

        assert INDEX_SYMBOLS["1001"] == "KS11"
        assert INDEX_SYMBOLS["2001"] == "KQ11"

    def test_krx_import_is_deferred(self):
        # Importing krx_data_client at module scope would make every host
        # without KRX credentials fail to load the package that exists to
        # survive KRX being unavailable.
        import inspect

        from cores.market_data import krx_source

        assert "import krx_data_client" not in inspect.getsource(krx_source).split(
            "def _client"
        )[0]

    def test_both_sources_satisfy_the_protocol(self):
        from cores.market_data.fdr_source import FdrSource
        from cores.market_data.krx_source import KrxSource

        for source in (KrxSource(), FdrSource()):
            for verb in (
                "price_history",
                "index_history",
                "market_cap_history",
                "investor_flows",
                "fundamentals",
                "ticker_name",
            ):
                assert callable(getattr(source, verb)), f"{source.name}.{verb}"


class TestFallbackLogging:
    """A broken source must be stated once, not restated on every call.

    Both halves matter and they pull against each other. Repeating the same
    warning per call buried everything else — five ticker lookups produced ten
    identical lines and a morning batch produces hundreds — and a deployment
    report read the `[FALLBACK]` line as evidence that company names had been
    demoted to ticker codes, when the name had in fact been returned.

    Going quiet is not the fix. A primary that fails all day has to appear in
    the log, or this becomes the silence the fallback line was added to catch.
    """

    def _chain(self):
        broken = FakeSource("krx", raises=Unavailable("no credentials"))
        working = FakeSource("fdr", result="삼성전자")
        return SourceChain([broken, working])

    def test_a_repeated_failure_is_warned_about_once(self, caplog):
        chain = self._chain()

        with caplog.at_level(logging.DEBUG, logger="cores.market_data.source"):
            for _ in range(5):
                chain.fetch("ticker_name", "005930")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        unavailable = [r for r in warnings if "unavailable" in r.getMessage()]

        assert len(unavailable) == 1, f"said it {len(unavailable)} times"

    def test_the_repeats_are_still_recorded_at_debug(self, caplog):
        """Demoted, not dropped — the detail is there when someone looks."""
        chain = self._chain()

        with caplog.at_level(logging.DEBUG, logger="cores.market_data.source"):
            for _ in range(3):
                chain.fetch("ticker_name", "005930")

        debug = [r for r in caplog.records if r.levelno == logging.DEBUG]

        assert debug, "the repeats vanished entirely"

    def test_a_failing_primary_is_never_entirely_silent(self, caplog):
        """The half that must not regress: one warning, not zero."""
        chain = self._chain()

        with caplog.at_level(logging.DEBUG, logger="cores.market_data.source"):
            chain.fetch("ticker_name", "005930")

        assert [r for r in caplog.records if r.levelno == logging.WARNING]

    def test_each_capability_is_announced_separately(self, caplog):
        """KRX failing prices and failing flows are two different facts."""
        broken = FakeSource("krx", raises=Unavailable("no credentials"))
        working = FakeSource("fdr", result="ok")
        chain = SourceChain([broken, working])

        with caplog.at_level(logging.DEBUG, logger="cores.market_data.source"):
            chain.fetch("ticker_name", "005930")
            chain.fetch("price_history", "005930", "20260101", "20260102")

        warned = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.WARNING and "unavailable" in r.getMessage()
        ]

        assert len(warned) == 2
        assert any("ticker_name" in m for m in warned)
        assert any("price_history" in m for m in warned)

    def test_the_fallback_line_does_not_repeat_either(self, caplog):
        """It reads as a failure; on a chain that answered, it is not one."""
        chain = self._chain()

        with caplog.at_level(logging.DEBUG, logger="cores.market_data.source"):
            for _ in range(4):
                assert chain.fetch("ticker_name", "005930") == "삼성전자"

        fallbacks = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "[FALLBACK]" in r.getMessage()
        ]

        assert len(fallbacks) == 1

    def test_a_fresh_chain_speaks_again(self, caplog):
        """The memory is per chain, so a new process starts by saying it once."""
        with caplog.at_level(logging.DEBUG, logger="cores.market_data.source"):
            self._chain().fetch("ticker_name", "005930")
            caplog.clear()
            self._chain().fetch("ticker_name", "005930")

        assert [r for r in caplog.records if r.levelno == logging.WARNING]


class TestStandingDownADeadSource:
    """A source whose credentials are refused fails on every single lookup.

    Warning once was already handled; this is about the cost of the call
    itself. On the production host KRX login is blocked, so `krx` — first in
    the default order — spent a full round-trip per lookup, hundreds of times
    a batch, before the chain moved on.
    """

    def test_a_dead_source_stops_being_called(self):
        dead = FakeSource("dead", raises=Unavailable("login blocked"))
        alive = FakeSource("alive", result=pd.DataFrame({"Close": [1]}))
        chain = SourceChain([dead, alive])

        for _ in range(10):
            chain.fetch("price_history", "005930", "20260101", "20260102")

        assert len(dead.calls) == SourceChain.RETIRE_AFTER_CONSECUTIVE_FAILURES
        assert len(alive.calls) == 10  # every lookup still answered

    def test_an_unsupported_capability_is_not_a_failure(self):
        """`Unsupported` is a settled answer about one capability, not illness —
        retiring on it would drop a source that answers everything else."""
        partial = FakeSource("partial", raises=Unsupported("no flows here"))
        alive = FakeSource("alive", result=pd.DataFrame({"Close": [1]}))
        chain = SourceChain([partial, alive])

        for _ in range(10):
            chain.fetch("price_history", "005930", "20260101", "20260102")

        assert len(partial.calls) == 10
        assert chain._consecutive_failures == {}

    def test_a_transient_blip_does_not_cost_the_primary_its_place(self):
        class Flaky:
            name = "flaky"

            def __init__(self):
                self.calls = []

            def price_history(self, ticker, start, end, *, adjusted=True):
                self.calls.append(ticker)
                if len(self.calls) <= 2:
                    raise Unavailable("blip")
                return pd.DataFrame({"Close": [1]})

        flaky = Flaky()
        chain = SourceChain([flaky, FakeSource("alive", result=pd.DataFrame({"Close": [1]}))])

        for _ in range(6):
            chain.fetch("price_history", "005930", "20260101", "20260102")

        # Recovered on the third call and kept answering: never retired.
        assert len(flaky.calls) == 6
        assert chain._consecutive_failures == {}

    def test_standing_a_source_down_is_announced_once(self, caplog):
        dead = FakeSource("dead", raises=Unavailable("login blocked"))
        chain = SourceChain([dead, FakeSource("alive", result=pd.DataFrame({"Close": [1]}))])

        with caplog.at_level(logging.WARNING):
            for _ in range(8):
                chain.fetch("price_history", "005930", "20260101", "20260102")

        stood_down = [r for r in caplog.records if "[SOURCE_DOWN]" in r.message]
        assert len(stood_down) == 1
