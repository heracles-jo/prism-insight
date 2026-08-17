"""Toss as a market data provider.

Written against the same protocol as the KRX, Naver and KIS sources, so it
joins the existing fallback chain without any consumer knowing it exists. The
port was defined by what the reports ask for rather than by any one provider's
API, which is what makes adding this a single file.

Toss covers two gaps worth noting. It publishes investor flows — the figure
only exchanges normally provide, and the reason KRX outages used to leave
charts blank — and it answers `ticker_name`, which the KIS source cannot. It
also publishes a provisional same-day flow record, which is the intraday
estimate under a different name.

What it does not cover is declared `Unsupported` rather than approximated.
Market cap is the tempting one: Toss gives `sharesOutstanding` and this module
has close prices, so a series could be multiplied out. It would also be wrong,
because the share count is today's and applying it to last year's price
silently rewrites every buyback and issuance in between. A missing number
becomes another source's problem; a plausible wrong one becomes nobody's.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from cores.market_data.schema import has_ohlcv, normalize
from cores.market_data.source import Unavailable, Unsupported

logger = logging.getLogger(__name__)

_CANDLES = "/api/v1/candles"
_INDICATOR_CANDLES = "/api/v1/market-indicators/{symbol}/candles"
_INVESTOR_TRADING = "/api/v1/stocks/{symbol}/investor-trading"
_STOCKS = "/api/v1/stocks"

# One request's worth. Toss paginates with `before`/`nextBefore`, so this only
# sets how many round trips a long range costs.
_PAGE = 200
_MAX_PAGES = 20

_PRICE_COLUMNS = {
    "openPrice": "Open",
    "highPrice": "High",
    "lowPrice": "Low",
    "closePrice": "Close",
    "volume": "Volume",
}

# PRISM identifies indices by KRX code; Toss uses names from its own catalogue.
_INDEX_SYMBOLS = {
    "1001": "KOSPI",
    "2001": "KOSDAQ",
}

# Toss nests each investor type under its own object; the reports read the
# Korean headers the exchange uses.
_FLOW_FIELDS = {
    "institution": "기관합계",
    "foreigner": "외국인합계",
    "individual": "개인",
    "otherCorporation": "기타법인",
}


class TossSource:
    """Market data from the Toss Open API."""

    name = "toss"

    def __init__(self, client: Any | None = None) -> None:
        self._client = client

    # ------------------------------------------------------------------ client

    def _api(self) -> Any:
        """The Toss client, built on first use.

        Configuration problems surface as `Unavailable` so an installation with
        no Toss credentials simply falls through to the next source, rather than
        failing a report that three other providers could have answered.
        """
        if self._client is not None:
            return self._client
        try:
            from trading.brokers import settings
            from trading.brokers.toss.auth import TossAuth, TossCredentials
            from trading.brokers.toss.client import TossClient

            config = settings.load_toss_config()
            self._client = TossClient(
                TossAuth(
                    TossCredentials(
                        config["client_id"],
                        config["client_secret"],
                        base_url=config.get("base_url") or "https://openapi.tossinvest.com",
                    )
                ),
                account_seq=config.get("account_seq"),
            )
        except Exception as exc:  # noqa: BLE001 - any setup failure means "skip me"
            raise Unavailable(f"Toss client unavailable: {exc}") from exc
        return self._client

    def _get(self, path: str, params: dict[str, Any], what: str) -> Any:
        from trading.brokers.base import BrokerUnavailable
        from trading.brokers.toss.errors import TossApiError

        try:
            return self._api().request("GET", path, params=params)
        except Unavailable:
            raise
        except (TossApiError, BrokerUnavailable) as exc:
            raise Unavailable(f"Toss {what} failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            raise Unavailable(f"Toss {what} failed: {exc}") from exc

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _frame(rows: list[dict], columns: dict[str, str], what: str) -> pd.DataFrame:
        """Rows to a date-indexed numeric frame in the shared schema."""
        if not rows:
            raise Unavailable(f"Toss returned no rows for {what}")

        frame = pd.DataFrame(rows)
        if "date" not in frame.columns:
            raise Unavailable(f"Toss {what} response has no date")

        present = {src: dst for src, dst in columns.items() if src in frame.columns}
        if not present:
            raise Unavailable(f"Toss {what} response has none of the expected fields")

        out = frame[["date", *present]].rename(columns=present)
        out.index = pd.to_datetime(out.pop("date"))
        # Every value arrives as a decimal string; a null is missing, not zero.
        out = out.apply(pd.to_numeric, errors="coerce")
        return normalize(out[~out.index.duplicated(keep="first")])

    def _walk_candles(self, path: str, params: dict[str, Any], start: str, what: str) -> list[dict]:
        """Page backwards until the window is covered.

        Toss returns newest-first with a `nextBefore` cursor. Stopping on an
        empty page alone would loop forever against a provider that keeps
        returning the same cursor, so progress is also required.
        """
        start_ts = pd.Timestamp(start)
        collected: dict[str, dict] = {}
        before: str | None = None

        for _ in range(_MAX_PAGES):
            page_params = dict(params, count=_PAGE)
            if before:
                page_params["before"] = before

            result = self._get(path, page_params, what) or {}
            candles = result.get("candles") if isinstance(result, dict) else None
            if not candles:
                break

            fresh = False
            earliest = None
            for candle in candles:
                stamp = candle.get("timestamp")
                if not stamp:
                    continue
                day = str(pd.Timestamp(stamp).tz_localize(None).normalize().date())
                if day not in collected:
                    collected[day] = dict(candle, date=day)
                    fresh = True
                earliest = day if earliest is None else min(earliest, day)

            if not fresh or earliest is None:
                break
            if pd.Timestamp(earliest) <= start_ts:
                break

            next_before = result.get("nextBefore")
            if not next_before or next_before == before:
                break
            before = next_before

        return list(collected.values())

    @staticmethod
    def _clip(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
        window = frame[
            (frame.index >= pd.Timestamp(start)) & (frame.index <= pd.Timestamp(end))
        ]
        if window.empty:
            raise Unavailable("Toss returned nothing inside the requested range")
        return window

    # ------------------------------------------------------------- capabilities

    def price_history(
        self, ticker: str, start: str, end: str, *, adjusted: bool = True
    ) -> pd.DataFrame:
        rows = self._walk_candles(
            _CANDLES,
            {"symbol": ticker, "interval": "1d", "adjusted": str(bool(adjusted)).lower()},
            start,
            f"candles {ticker}",
        )
        frame = self._frame(rows, _PRICE_COLUMNS, f"ohlcv {ticker}")
        if not has_ohlcv(frame):
            raise Unavailable(f"Toss ohlcv {ticker} missing OHLCV columns")
        return self._clip(frame, start, end)

    def index_history(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        symbol = _INDEX_SYMBOLS.get(str(index_code))
        if symbol is None:
            # Guessing would return a real number for a different index, which
            # is worse than not answering.
            raise Unsupported(f"no Toss market indicator mapped for index {index_code}")

        rows = self._walk_candles(
            _INDICATOR_CANDLES.format(symbol=symbol),
            {"interval": "1d"},
            start,
            f"index candles {symbol}",
        )
        frame = self._frame(rows, _PRICE_COLUMNS, f"index {symbol}")
        return self._clip(frame, start, end)

    def investor_flows(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        rows = self._walk_flows(ticker, start)
        frame = self._frame(rows, {v: v for v in _FLOW_FIELDS.values()}, f"flows {ticker}")
        return self._clip(frame, start, end)

    def _walk_flows(self, ticker: str, start: str, *, include_provisional: bool = False) -> list[dict]:
        start_ts = pd.Timestamp(start)
        collected: dict[str, dict] = {}
        until: str | None = None

        for _ in range(_MAX_PAGES):
            params: dict[str, Any] = {"count": _PAGE}
            if until:
                params["until"] = until

            result = self._get(
                _INVESTOR_TRADING.format(symbol=ticker), params, f"investor flows {ticker}"
            ) or {}
            records = result.get("records") if isinstance(result, dict) else None
            if not records:
                break

            fresh = False
            earliest = None
            for record in records:
                day = record.get("date")
                if not day or day in collected:
                    if day:
                        earliest = day if earliest is None else min(earliest, day)
                    continue
                row = self._flow_row(record, include_provisional=include_provisional)
                if row is not None:
                    collected[day] = row
                    fresh = True
                earliest = day if earliest is None else min(earliest, day)

            if earliest is None or pd.Timestamp(earliest) <= start_ts:
                break
            next_until = result.get("nextUntil")
            if not next_until or next_until == until:
                break
            if not fresh and next_until == until:
                break
            until = next_until

        return list(collected.values())

    @staticmethod
    def _flow_row(record: dict, *, include_provisional: bool) -> dict | None:
        """One day's net buying, or None when the day is not usable.

        Toss publishes a same-day record before the numbers settle, with the
        investor types that are not final left as null. Treating those nulls as
        zero would show a day of no institutional activity, so a provisional
        record is skipped for history and only used where it is asked for.
        """
        values: dict[str, Any] = {"date": record.get("date")}
        populated = 0
        for field, column in _FLOW_FIELDS.items():
            block = record.get(field)
            if isinstance(block, dict) and block.get("netBuyVolume") is not None:
                values[column] = block["netBuyVolume"]
                populated += 1

        if populated == 0:
            return None
        if not include_provisional and populated < 2:
            # A record with almost everything still null is the provisional one.
            return None
        return values

    def intraday_investor_estimate(
        self, ticker: str, *, as_of: Any = None
    ) -> pd.DataFrame:
        """Toss's provisional same-day record, which is this under another name."""
        today = pd.Timestamp(as_of).normalize() if as_of is not None else pd.Timestamp.now().normalize()
        rows = self._walk_flows(ticker, str(today.date()), include_provisional=True)
        same_day = [row for row in rows if pd.Timestamp(row["date"]).normalize() == today]
        if not same_day:
            raise Unavailable(f"Toss has no provisional flow record for {ticker} today")
        return self._frame(same_day, {v: v for v in _FLOW_FIELDS.values()}, f"estimate {ticker}")

    def market_cap_history(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        # `sharesOutstanding` is a current figure. Multiplying it by historical
        # closes would restate every buyback and issuance in the window as if it
        # had never happened, and the result would look entirely reasonable.
        raise Unsupported("Toss publishes shares outstanding as a current value, not a series")

    def fundamentals(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        raise Unsupported("Toss does not publish a PER/PBR series")

    def ticker_name(self, ticker: str) -> str:
        result = self._get(_STOCKS, {"symbols": ticker}, f"stock info {ticker}")
        rows = result if isinstance(result, list) else [result]
        for row in rows:
            if isinstance(row, dict) and str(row.get("symbol")) == ticker:
                name = str(row.get("name") or "").strip()
                if name:
                    return name
        raise Unavailable(f"Toss returned no name for {ticker}")
