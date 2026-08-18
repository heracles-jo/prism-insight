"""Recent investor flows from Naver Finance's public stock trend endpoint.

This is a narrow resilience adapter, not a general market-data source. The
endpoint exposes only the latest ten sessions, but that is enough to keep a
same-day report honest about recent foreign, institutional, and individual net
buying when authenticated KRX is unavailable and KIS is closed until 15:40.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

import pandas as pd
import requests

from cores.market_data.schema import normalize
from cores.market_data.source import Unavailable, Unsupported

logger = logging.getLogger(__name__)

_TREND_URL = "https://m.stock.naver.com/api/stock/{ticker}/trend"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://m.stock.naver.com/",
}
_COLUMNS = {
    "organPureBuyQuant": "기관합계",
    "foreignerPureBuyQuant": "외국인합계",
    "individualPureBuyQuant": "개인",
}

# Sector classification. Naver serves an index of industries and, per industry,
# the stocks in it — so the map costs one request plus one per industry (~79).
_SECTOR_INDEX_URL = "https://finance.naver.com/sise/sise_group.naver?type=upjong"
_SECTOR_DETAIL_URL = (
    "https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={no}"
)
# These pages are EUC-KR and are not served with a usable charset header.
_SECTOR_ENCODING = "euc-kr"
_SECTOR_LINK = re.compile(
    r'sise_group_detail\.naver\?type=upjong&no=(\d+)">([^<]+)<'
)
_SECTOR_MEMBER = re.compile(r'/item/main\.naver\?code=(\d{6})"')


class NaverSource:
    name = "naver"

    def __init__(self, *, request_get: Callable = requests.get) -> None:
        self._request_get = request_get
        # Built once per process: ~80 requests, and the classification only
        # changes when a company is reclassified.
        self._sector_map: dict[str, str] | None = None

    def investor_flows(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        try:
            response = self._request_get(
                _TREND_URL.format(ticker=ticker),
                headers=_HEADERS,
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise Unavailable(f"Naver trend request failed: {exc}") from exc

        if not isinstance(payload, list) or not payload:
            raise Unavailable("Naver trend returned no rows")

        records = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            date = str(row.get("bizdate") or "")
            if len(date) != 8 or not date.isdigit():
                continue
            try:
                record = {"Date": date}
                record.update(
                    {
                        target: _signed_int(row.get(source))
                        for source, target in _COLUMNS.items()
                    }
                )
            except (TypeError, ValueError):
                continue
            records.append(record)

        if not records:
            raise Unavailable("Naver trend rows failed schema validation")

        frame = pd.DataFrame.from_records(records).set_index("Date")
        frame = normalize(frame)
        frame = frame[
            (frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))
        ]
        if frame.empty:
            raise Unavailable(f"Naver trend has no rows in {start}..{end}")
        frame.attrs["coverage"] = "latest_10_sessions"
        return frame

    def price_history(self, ticker: str, start: str, end: str, *, adjusted: bool = True):
        raise Unsupported("Naver trend source only publishes recent investor flows")

    def index_history(self, index_code: str, start: str, end: str):
        raise Unsupported("Naver trend source only publishes recent investor flows")

    def market_cap_history(self, ticker: str, start: str, end: str):
        raise Unsupported("Naver trend source only publishes recent investor flows")

    def fundamentals(self, ticker: str, start: str, end: str):
        raise Unsupported("Naver trend source only publishes recent investor flows")

    def ticker_name(self, ticker: str):
        raise Unsupported("Naver trend source only publishes recent investor flows")

    def sector_map(self, market: str) -> dict[str, str]:
        """Ticker to industry name for every classified stock.

        `market` is accepted for contract compatibility and does not narrow the
        result: Naver classifies by industry, not by board, so one page mixes
        KOSPI and KOSDAQ members. Every caller either merges both markets or
        looks a ticker up directly, so returning the whole map is what they
        want anyway — and it keeps the two calls a caller makes down to one
        fetch.

        Note that this taxonomy is not KRX's. Naver publishes finer,
        differently named industries (반도체와반도체장비, 해운사) where KRX has
        broad ones (전기·전자). That is safe here because the macro agent
        derives its sector vocabulary from these same values, so the names it
        chooses and the names it matches against always come from one source.
        """
        if self._sector_map is not None:
            return self._sector_map

        index_html = self._sector_page(_SECTOR_INDEX_URL, "sector index")
        industries = _SECTOR_LINK.findall(index_html)
        if not industries:
            raise Unavailable("Naver sector index listed no industries")

        mapping: dict[str, str] = {}
        for number, name in industries:
            label = name.strip()
            if not label:
                continue
            try:
                page = self._sector_page(
                    _SECTOR_DETAIL_URL.format(no=number), f"sector {label}"
                )
            except Unavailable:
                # One industry failing should cost that industry, not the map.
                logger.warning("[naver] sector page %s (%s) failed", number, label)
                continue
            for code in _SECTOR_MEMBER.findall(page):
                mapping.setdefault(code, label)

        if not mapping:
            raise Unavailable("Naver sector pages yielded no tickers")

        self._sector_map = mapping
        return mapping

    def _sector_page(self, url: str, what: str) -> str:
        try:
            response = self._request_get(url, headers=_HEADERS, timeout=10)
            response.raise_for_status()
        except Exception as exc:
            raise Unavailable(f"Naver {what} request failed: {exc}") from exc

        # Set explicitly: these pages declare no usable charset, so requests
        # guesses ISO-8859-1 and every Korean name comes back as mojibake.
        response.encoding = _SECTOR_ENCODING
        return response.text


def _signed_int(value: object) -> int:
    if not isinstance(value, (str, int)):
        raise TypeError("investor flow is not numeric")
    return int(str(value).replace(",", "").replace("+", "").strip())

