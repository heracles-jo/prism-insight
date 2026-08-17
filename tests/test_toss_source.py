"""Toss market data source: does it fit the chain without consumers noticing?

The protocol's whole value is that a source can be added without editing call
sites, so the tests check conformance to the shared schema — column names,
index type, numeric dtypes — rather than that the HTTP calls happen.

The other emphasis is on what the source refuses to do. `Unsupported` and
`Unavailable` mean different things to `SourceChain`, and a source that
approximates a figure it does not have poisons every downstream chart while
looking healthy.
"""

import pandas as pd
import pytest

from cores.market_data.source import MarketDataSource, Unavailable, Unsupported


def candles(days, start_price=70000):
    """Newest-first, as Toss returns them."""
    out = []
    for i, day in enumerate(days):
        price = start_price + i * 100
        out.append({
            "timestamp": f"{day}T09:00:00+09:00",
            "openPrice": str(price), "highPrice": str(price + 500),
            "lowPrice": str(price - 500), "closePrice": str(price + 200),
            "volume": "3521000", "currency": "KRW",
        })
    return out


def flow_record(date, individual="291850", foreigner="-319700", institution="37900",
                other="1000"):
    def block(v):
        return None if v is None else {"buyVolume": "0", "sellVolume": "0", "netBuyVolume": v}
    return {
        "date": date, "updatedAt": f"{date}T15:40:00+09:00",
        "individual": block(individual), "foreigner": block(foreigner),
        "institution": block(institution), "otherCorporation": block(other),
    }


class StubClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, *, params=None, **kwargs):
        self.calls.append((method, path, params))
        for key, value in self.responses.items():
            if path.startswith(key):
                if isinstance(value, Exception):
                    raise value
                return value(params) if callable(value) else value
        raise AssertionError(f"unexpected path {path}")


def make_source(responses):
    from cores.market_data.toss_source import TossSource

    client = StubClient(responses)
    return TossSource(client), client


# ── Protocol conformance ─────────────────────────────────────────────────────


def test_it_satisfies_the_market_data_protocol():
    source, _ = make_source({})
    assert isinstance(source, MarketDataSource)
    assert source.name == "toss"


def test_it_can_join_a_source_chain():
    """The success signal: registering it changes nothing for consumers."""
    from cores.market_data.source import SourceChain

    source, _ = make_source({
        "/api/v1/candles": {"candles": candles(["2026-08-14", "2026-08-13"])},
    })
    chain = SourceChain([source])

    frame = chain.fetch("price_history", "005930", "20260813", "20260814")
    assert not frame.empty
    assert "toss" in chain.names


def test_the_chain_moves_on_when_toss_is_unconfigured():
    """No credentials must not fail a report three other sources could answer."""
    from cores.market_data.source import SourceChain
    from cores.market_data.toss_source import TossSource

    class Backup:
        name = "backup"

        def price_history(self, *args, **kwargs):
            return pd.DataFrame({"Close": [1]}, index=[pd.Timestamp("2026-08-14")])

    # No client injected and no config on disk → Unavailable, not a crash.
    chain = SourceChain([TossSource(), Backup()])
    assert chain.fetch("price_history", "005930", "20260813", "20260814").iloc[0]["Close"] == 1


# ── Price history ────────────────────────────────────────────────────────────


def test_price_history_returns_the_shared_schema():
    source, _ = make_source({
        "/api/v1/candles": {"candles": candles(["2026-08-14", "2026-08-13", "2026-08-12"])},
    })

    frame = source.price_history("005930", "20260812", "20260814")
    assert {"Open", "High", "Low", "Close", "Volume"}.issubset(frame.columns)
    assert isinstance(frame.index, pd.DatetimeIndex)
    # Numeric, not string — whether it lands on int or float depends on the
    # values, and both are fine for arithmetic downstream.
    assert frame["Close"].dtype.kind in "if"
    assert frame["Volume"].dtype.kind in "if"


def test_price_history_is_sorted_oldest_first():
    """Toss answers newest-first; every indicator downstream assumes ascending."""
    source, _ = make_source({
        "/api/v1/candles": {"candles": candles(["2026-08-14", "2026-08-13", "2026-08-12"])},
    })

    frame = source.price_history("005930", "20260812", "20260814")
    assert list(frame.index) == sorted(frame.index)


def test_price_history_is_clipped_to_the_requested_window():
    source, _ = make_source({
        "/api/v1/candles": {
            "candles": candles(["2026-08-14", "2026-08-13", "2026-08-12", "2026-08-11"])
        },
    })

    frame = source.price_history("005930", "20260812", "20260813")
    assert str(frame.index.min().date()) == "2026-08-12"
    assert str(frame.index.max().date()) == "2026-08-13"


def test_the_adjusted_flag_reaches_the_api():
    """Unadjusted prices would silently break every indicator across a split."""
    source, client = make_source({
        "/api/v1/candles": {"candles": candles(["2026-08-14"])},
    })

    source.price_history("005930", "20260814", "20260814")
    assert client.calls[0][2]["adjusted"] == "true"

    client.calls.clear()
    source.price_history("005930", "20260814", "20260814", adjusted=False)
    assert client.calls[0][2]["adjusted"] == "false"


def test_pagination_walks_back_until_the_window_is_covered():
    pages = [
        {"candles": candles(["2026-08-14", "2026-08-13"]), "nextBefore": "2026-08-13T09:00:00+09:00"},
        {"candles": candles(["2026-08-12", "2026-08-11"]), "nextBefore": None},
    ]

    def serve(params):
        return pages.pop(0) if pages else {"candles": []}

    source, _ = make_source({"/api/v1/candles": serve})
    frame = source.price_history("005930", "20260811", "20260814")
    assert len(frame) == 4


def test_pagination_stops_when_the_cursor_stops_moving():
    """A provider repeating its cursor must not spin forever."""
    stuck = {"candles": candles(["2026-08-14"]), "nextBefore": "same"}
    source, client = make_source({"/api/v1/candles": stuck})

    source.price_history("005930", "20260101", "20260814")
    assert len(client.calls) < 5


def test_an_empty_response_is_unavailable_not_an_empty_frame():
    """An empty frame renders as a blank chart instead of an error."""
    source, _ = make_source({"/api/v1/candles": {"candles": []}})

    with pytest.raises(Unavailable):
        source.price_history("005930", "20260812", "20260814")


def test_a_response_missing_ohlcv_is_unavailable():
    source, _ = make_source({
        "/api/v1/candles": {"candles": [{"timestamp": "2026-08-14T09:00:00+09:00",
                                         "volume": "100"}]},
    })

    with pytest.raises(Unavailable):
        source.price_history("005930", "20260814", "20260814")


def test_an_api_failure_becomes_unavailable():
    from trading.brokers.toss.errors import TossApiError

    source, _ = make_source({"/api/v1/candles": TossApiError("boom", status=500)})

    with pytest.raises(Unavailable):
        source.price_history("005930", "20260812", "20260814")


# ── Index history ────────────────────────────────────────────────────────────


def test_index_codes_map_to_toss_symbols():
    source, client = make_source({
        "/api/v1/market-indicators": {"candles": candles(["2026-08-14"], 2800)},
    })

    source.index_history("1001", "20260814", "20260814")
    assert "KOSPI" in client.calls[0][1]

    client.calls.clear()
    source.index_history("2001", "20260814", "20260814")
    assert "KOSDAQ" in client.calls[0][1]


def test_an_unmapped_index_is_unsupported_not_guessed():
    """A guess returns a real number for the wrong index."""
    source, _ = make_source({})

    with pytest.raises(Unsupported):
        source.index_history("9999", "20260814", "20260814")


# ── Investor flows ───────────────────────────────────────────────────────────


def test_investor_flows_use_the_exchange_column_names():
    source, _ = make_source({
        "/api/v1/stocks/005930/investor-trading": {
            "records": [flow_record("2026-08-14"), flow_record("2026-08-13")],
        },
    })

    frame = source.investor_flows("005930", "20260813", "20260814")
    assert {"기관합계", "외국인합계", "개인", "기타법인"}.issubset(frame.columns)
    assert frame.loc[pd.Timestamp("2026-08-14"), "외국인합계"] == -319700


def test_a_provisional_same_day_record_is_excluded_from_history():
    """Its nulls would read as a day of no institutional activity."""
    source, _ = make_source({
        "/api/v1/stocks/005930/investor-trading": {
            "records": [
                flow_record("2026-08-14", individual=None, institution=None, other=None),
                flow_record("2026-08-13"),
            ],
        },
    })

    frame = source.investor_flows("005930", "20260813", "20260814")
    assert list(frame.index) == [pd.Timestamp("2026-08-13")]


def test_the_intraday_estimate_uses_the_provisional_record():
    source, _ = make_source({
        "/api/v1/stocks/005930/investor-trading": {
            "records": [
                flow_record("2026-08-14", individual=None, institution=None, other=None),
            ],
        },
    })

    frame = source.intraday_investor_estimate("005930", as_of=pd.Timestamp("2026-08-14"))
    assert frame.loc[pd.Timestamp("2026-08-14"), "외국인합계"] == -319700


def test_no_provisional_record_is_unavailable():
    source, _ = make_source({
        "/api/v1/stocks/005930/investor-trading": {"records": [flow_record("2026-08-13")]},
    })

    with pytest.raises(Unavailable):
        source.intraday_investor_estimate("005930", as_of=pd.Timestamp("2026-08-14"))


# ── What it refuses ──────────────────────────────────────────────────────────


def test_market_cap_is_unsupported_rather_than_derived():
    """Shares outstanding is today's; applying it to old closes rewrites history."""
    source, _ = make_source({})

    with pytest.raises(Unsupported):
        source.market_cap_history("005930", "20260101", "20260814")


def test_fundamentals_are_unsupported():
    source, _ = make_source({})

    with pytest.raises(Unsupported):
        source.fundamentals("005930", "20260101", "20260814")


def test_unsupported_lets_the_chain_try_the_next_source():
    """The distinction that makes the chain work at all."""
    from cores.market_data.source import SourceChain

    source, _ = make_source({})

    class Backup:
        name = "backup"

        def market_cap_history(self, *args):
            return pd.DataFrame({"MarketCap": [42]}, index=[pd.Timestamp("2026-08-14")])

    chain = SourceChain([source, Backup()])
    answer = chain.fetch("market_cap_history", "005930", "20260101", "20260814")
    assert answer["MarketCap"].iloc[0] == 42


# ── Ticker name, which the KIS source cannot answer ──────────────────────────


def test_ticker_name_is_answered():
    source, _ = make_source({
        "/api/v1/stocks": [{"symbol": "005930", "name": "삼성전자", "market": "KOSPI"}],
    })

    assert source.ticker_name("005930") == "삼성전자"


def test_a_name_for_a_different_symbol_is_not_returned():
    source, _ = make_source({
        "/api/v1/stocks": [{"symbol": "000660", "name": "SK하이닉스"}],
    })

    with pytest.raises(Unavailable):
        source.ticker_name("005930")
