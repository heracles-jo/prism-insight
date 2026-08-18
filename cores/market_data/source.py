"""What a market data provider has to offer, and how several are combined.

The protocol is written from what the reports actually ask for, not from any
one provider's API. That is deliberate: define the port by the need and each
provider becomes an adapter, so adding a broker means writing one class rather
than editing every call site.

No provider covers everything. FinanceDataReader has no investor flows; a broker
may have flows but no index history. Rather than returning something plausible
but wrong, a source declares a capability unsupported and the chain moves on —
which is also how a caller learns that nobody can answer, instead of receiving
an empty frame that renders as a blank chart.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

import pandas as pd

logger = logging.getLogger(__name__)


class Unsupported(Exception):
    """This provider does not offer this capability at all.

    Distinct from failure: there is nothing to retry and nothing is wrong. The
    chain skips to the next source without logging an error.
    """


class Unavailable(Exception):
    """This provider could not answer right now.

    Restriction, timeout, auth, an empty result — all the same to the chain,
    which tries the next source. The message is kept for the log because the
    2026-08-04 outage was prolonged by a failure that reported only "not found".
    """


@runtime_checkable
class MarketDataSource(Protocol):
    """One provider of per-instrument market data.

    Implementations raise `Unsupported` for capabilities they lack and
    `Unavailable` when a call fails. Returning an empty frame is not an
    acceptable way to signal either.
    """

    name: str

    def price_history(
        self, ticker: str, start: str, end: str, *, adjusted: bool = True
    ) -> pd.DataFrame:
        """Daily OHLCV for one stock. Dates are `YYYYMMDD`."""

    def index_history(self, index_code: str, start: str, end: str) -> pd.DataFrame:
        """Daily OHLCV for an index, keyed by the KRX index code."""

    def market_cap_history(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Market capitalisation over time, column `MarketCap`."""

    def investor_flows(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """Net buying by investor type — the figure only exchanges publish."""

    def intraday_investor_estimate(
        self, ticker: str, *, as_of=None
    ) -> pd.DataFrame:
        """Current-session investor-flow estimate, when a provider publishes it."""

    def fundamentals(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        """PER/PBR/dividend yield over time."""

    def ticker_name(self, ticker: str) -> str:
        """Company name for a ticker."""

    def sector_map(self, market: str) -> dict[str, str]:
        """Ticker to sector name, for every listed stock.

        The only capability that answers about the whole market rather than one
        instrument, which is why it takes a market instead of a ticker.
        """


class SourceChain:
    """Ask each source in turn until one answers.

    Order is the caller's choice rather than a constant in this file, so the
    same code can run KRX-first in production and broker-first once the
    migration lands, without an edit here.
    """

    def __init__(self, sources: list[MarketDataSource]) -> None:
        if not sources:
            raise ValueError("a chain needs at least one source")
        self._sources = sources
        # What has already been said. A first source that is down stays down for
        # the whole run, so repeating it per call buries everything else: five
        # ticker lookups produced ten identical warnings, and a morning batch
        # produces hundreds. Reported once at WARNING, then demoted — the state
        # is still stated, just not restated.
        self._announced: set[tuple[str, str, str]] = set()
        # Acting on the observation above rather than only quieting it. A source
        # whose credentials are refused (KRX login is blocked on the production
        # host) failed on *every* lookup, so each one paid a full round-trip
        # before the chain moved on — a morning batch spends that hundreds of
        # times. After enough consecutive failures the source is stood down for
        # the rest of the process; a fresh batch gives it another chance.
        self._consecutive_failures: dict[str, int] = {}

    # Three, not one: a single timeout or 502 should not cost the primary its
    # place for a whole batch. A dead source reaches three almost immediately.
    RETIRE_AFTER_CONSECUTIVE_FAILURES = 3

    def _announce(self, kind: str, source_name: str, capability: str, message: str, *args) -> None:
        """WARNING the first time this exact situation occurs, DEBUG after.

        Silence is not the alternative to noise here: a chronically failing
        primary source has to appear in the log, or this becomes the problem the
        `[FALLBACK]` line was added to catch.
        """
        key = (kind, source_name, capability)
        if key in self._announced:
            logger.debug(message, *args)
            return
        self._announced.add(key)
        logger.warning(message, *args)

    def _record_failure(self, source_name: str) -> None:
        """Count a failure, and say so once when it retires the source.

        `Unsupported` deliberately does not come through here: it is a settled
        answer about one capability, not a sign the source is unwell.
        """
        count = self._consecutive_failures.get(source_name, 0) + 1
        self._consecutive_failures[source_name] = count
        if count == self.RETIRE_AFTER_CONSECUTIVE_FAILURES:
            logger.warning(
                "[SOURCE_DOWN] %s failed %d times in a row; standing it down for "
                "the rest of this run. Later sources answer from here on.",
                source_name, count,
            )

    @property
    def names(self) -> list[str]:
        return [s.name for s in self._sources]

    def fetch(self, capability: str, *args, **kwargs) -> pd.DataFrame | str | dict:
        """Call `capability` on each source until one succeeds.

        Raises `Unavailable` listing every attempt when none can answer. The
        alternative — returning empty — is what let a two-hour outage look like
        "these stocks have no data".
        """
        attempts: list[str] = []
        for source in self._sources:
            failures = self._consecutive_failures.get(source.name, 0)
            if failures >= self.RETIRE_AFTER_CONSECUTIVE_FAILURES:
                attempts.append(f"{source.name}: stood down after {failures} failures")
                continue
            method = getattr(source, capability, None)
            if method is None:
                attempts.append(f"{source.name}: no such capability")
                continue
            try:
                result = method(*args, **kwargs)
            except Unsupported:
                attempts.append(f"{source.name}: unsupported")
                continue
            except Unavailable as exc:
                attempts.append(f"{source.name}: {exc}")
                self._announce(
                    "unavailable", source.name, capability,
                    "%s unavailable for %s; trying next source", source.name, capability,
                )
                self._record_failure(source.name)
                continue
            except Exception as exc:  # noqa: BLE001 - a broken source must not stop the chain
                attempts.append(f"{source.name}: {type(exc).__name__}: {exc}")
                self._announce(
                    "raised", source.name, capability,
                    "%s raised on %s (%s); trying next source",
                    source.name, capability, exc,
                )
                self._record_failure(source.name)
                continue

            # Answered, so whatever was wrong is over.
            self._consecutive_failures.pop(source.name, None)

            if source is not self._sources[0]:
                # The call succeeded. Warning on it reported a working system as
                # broken — a deployment report read this line as evidence that
                # company names had been demoted to ticker codes, when the name
                # had in fact been returned. The reason the primary failed is
                # already stated above, once, at WARNING.
                self._announce(
                    "fallback", source.name, capability,
                    "[FALLBACK] %s answered %s after %s",
                    source.name, capability, ", ".join(attempts) or "primary",
                )
            return result

        raise Unavailable(f"{capability}: no source could answer ({'; '.join(attempts)})")
