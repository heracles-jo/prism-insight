"""Sector classification, the one capability with a single provider.

An empty sector map is not a cosmetic gap. `trigger_batch._build_topdown_pool`
starts with `if not sector_map: return []`, and `trigger_macro_sector_leader`
skips every ticker whose sector is blank — so without it the top-down half of
stock selection produces nothing while the batch still reports success. That is
the state the repo was in: KRX login broke, `get_sector_info` returned an error,
and the only visible trace was one warning line.

Naver is the only source that publishes this without credentials. KRX needs the
login this chain exists to avoid, FinanceDataReader's Sector column holds
listing tiers (벤처기업부) rather than industries, and Toss carries none at all —
all three measured.
"""

from __future__ import annotations

import pytest

from cores.market_data.naver_source import NaverSource
from cores.market_data.source import Unavailable, Unsupported


class FakeResponse:
    def __init__(self, text: str, *, status_error: Exception | None = None):
        self.text = text
        self.encoding = None
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error


_INDEX = (
    '<a href="/sise/sise_group_detail.naver?type=upjong&no=278">반도체와반도체장비</a>'
    '<a href="/sise/sise_group_detail.naver?type=upjong&no=330">생명보험</a>'
)
_SEMIS = '<a href="/item/main.naver?code=005930">삼성전자</a>' \
         '<a href="/item/main.naver?code=000660">SK하이닉스</a>'
_INSURERS = '<a href="/item/main.naver?code=032830">삼성생명</a>'


def _pages(**overrides):
    """Index page plus one page per industry, keyed by the `no=` parameter."""
    pages = {"index": _INDEX, "278": _SEMIS, "330": _INSURERS}
    pages.update(overrides)
    return pages


def _source(pages, *, fail_for: set[str] | None = None):
    calls: list[str] = []
    fail_for = fail_for or set()

    def get(url, **kwargs):
        calls.append(url)
        key = "index" if "sise_group.naver" in url else url.rsplit("no=", 1)[-1]
        if key in fail_for:
            return FakeResponse("", status_error=RuntimeError("boom"))
        return FakeResponse(pages.get(key, ""))

    return NaverSource(request_get=get), calls


def test_the_map_covers_every_industry_page():
    source, _ = _source(_pages())

    mapping = source.sector_map("KOSPI")

    assert mapping == {
        "005930": "반도체와반도체장비",
        "000660": "반도체와반도체장비",
        "032830": "생명보험",
    }


def test_pages_are_decoded_as_euc_kr():
    """These pages declare no usable charset, so requests guesses Latin-1.

    Left alone, every sector name arrives as mojibake and the macro agent's
    vocabulary becomes unreadable garbage that still matches nothing.
    """
    seen = []

    def get(url, **kwargs):
        response = FakeResponse(_INDEX if "sise_group.naver" in url else _SEMIS)
        seen.append(response)
        return response

    NaverSource(request_get=get).sector_map("KOSPI")

    assert seen and all(r.encoding == "euc-kr" for r in seen)


def test_one_bad_industry_page_costs_only_that_industry():
    """A single failure must not throw away the other seventy-eight."""
    source, _ = _source(_pages(), fail_for={"330"})

    mapping = source.sector_map("KOSPI")

    assert set(mapping) == {"005930", "000660"}


def test_an_unreadable_index_is_unavailable_not_an_empty_map():
    """Empty reads downstream as "no stock has a sector", which silently
    disables top-down selection instead of failing."""
    source, _ = _source({"index": "<html>nothing here</html>"})

    with pytest.raises(Unavailable):
        source.sector_map("KOSPI")


def test_industries_with_no_members_leave_the_map_empty_and_unavailable():
    source, _ = _source(_pages(**{"278": "", "330": ""}))

    with pytest.raises(Unavailable):
        source.sector_map("KOSPI")


def test_the_map_is_built_once_per_process():
    """~80 requests is worth paying once and not again on the second call."""
    source, calls = _source(_pages())

    source.sector_map("KOSPI")
    first = len(calls)
    source.sector_map("KOSDAQ")

    assert first == 3  # index + two industries
    assert len(calls) == first, "the second call went back to the network"


def test_the_market_argument_does_not_narrow_the_result():
    """Naver classifies by industry, not by board, so one page mixes markets.

    Documented rather than faked: pretending to filter would drop KOSDAQ
    tickers that `trigger_batch` then looks up and fails to find.
    """
    source, _ = _source(_pages())

    assert source.sector_map("KOSPI") == source.sector_map("KOSDAQ")


@pytest.mark.parametrize("module_name", ["toss_source", "krx_source", "fdr_source", "kis_source"])
def test_sources_without_sectors_say_so_rather_than_returning_empty(module_name):
    """`Unsupported` lets the chain move on; an empty dict would end the search.

    krx_source raised NameError here at first — the stub referenced a symbol the
    module never imported, which the chain reported as a source "raising" rather
    than as a missing capability.
    """
    import importlib

    module = importlib.import_module(f"cores.market_data.{module_name}")
    cls = next(
        value for name, value in vars(module).items()
        if name.endswith("Source") and isinstance(value, type)
    )

    with pytest.raises(Unsupported):
        cls.sector_map(object.__new__(cls), "KOSPI")
