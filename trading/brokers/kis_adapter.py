"""KIS behind the broker contract.

A delegating wrapper and nothing more. Every method hands its arguments to the
underlying trader unchanged and returns whatever comes back, by identity — no
copying, no key renaming, no defaulting. That is not laziness; it is the point.
This phase has to leave KIS behaviour bit-identical, and the cheapest way to be
sure of that is for the wrapper to have no behaviour of its own to get wrong.

The trader is injected rather than constructed here. Two reasons: constructing
it would drag `kis_auth` into import time and with it the credential files, and
the dry-run simulator needs to occupy this same slot later without this module
knowing about it.

`__getattr__` would have been shorter. It is avoided on purpose — the contract
is meant to be legible as a list of methods, and a catch-all forwards typos
straight through to the trader as silent no-ops.
"""

from __future__ import annotations

import logging
from typing import Any

from trading.brokers.base import BrokerUnsupported, HoldingState, OrderOutcome

logger = logging.getLogger(__name__)

_MARKETS = {"KR", "US"}


class KisBroker:
    """Wrap a KIS trader so it satisfies `BrokerPort`.

    Accepts a single-account trader (`DomesticStockTrading`, `USStockTrading`)
    or a multi-account one (`MultiAccountDomesticStockTrading`,
    `MultiAccountUSStockTrading`) — they expose the same method set, so the
    wrapper does not care which it holds.
    """

    name = "kis"

    def __init__(self, trader: Any, *, market: str):
        normalized = str(market).upper()
        if normalized not in _MARKETS:
            raise ValueError(f"market must be one of {sorted(_MARKETS)}, got {market!r}")
        self._trader = trader
        self.market = normalized
        logger.debug(
            "[BROKER] wrapped broker=%s market=%s trader=%s",
            self.name,
            self.market,
            type(trader).__name__,
        )

    @property
    def trader(self) -> Any:
        """The wrapped trader, for callers still reaching past the contract."""
        return self._trader

    # ── Orders ────────────────────────────────────────────────────────────────

    async def async_buy_stock(self, *args: Any, **kwargs: Any) -> OrderOutcome:
        return await self._trader.async_buy_stock(*args, **kwargs)

    async def async_sell_stock(self, *args: Any, **kwargs: Any) -> OrderOutcome:
        return await self._trader.async_sell_stock(*args, **kwargs)

    def amend_order(self, *args: Any, **kwargs: Any) -> OrderOutcome:
        return self._trader.amend_order(*args, **kwargs)

    def cancel_order(self, *args: Any, **kwargs: Any) -> OrderOutcome:
        return self._trader.cancel_order(*args, **kwargs)

    def buy_reserved_order(self, *args: Any, **kwargs: Any) -> OrderOutcome:
        return self._trader.buy_reserved_order(*args, **kwargs)

    def sell_reserved_order(self, *args: Any, **kwargs: Any) -> OrderOutcome:
        # KIS spells the domestic variant `sell_all_reserved_order`; US uses
        # `sell_reserved_order`. Prefer the contract name and fall back, rather
        # than making the caller know which trader it is holding.
        method = getattr(self._trader, "sell_reserved_order", None)
        if method is None:
            method = self._trader.sell_all_reserved_order
        return method(*args, **kwargs)

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_current_price(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        return self._trader.get_current_price(*args, **kwargs)

    def get_portfolio(self) -> list[dict[str, Any]]:
        return self._trader.get_portfolio()

    def get_account_summary(self) -> dict[str, Any]:
        return self._trader.get_account_summary()

    def get_holding_quantity(self, *args: Any, **kwargs: Any) -> int:
        return self._trader.get_holding_quantity(*args, **kwargs)

    def get_holding_quantity_checked(self, *args: Any, **kwargs: Any) -> HoldingState:
        # Only the domestic trader implements this; the US one exposes just the
        # collapsing `get_holding_quantity`. Surfacing that as `BrokerUnsupported`
        # is the honest move — synthesising a three-state answer here would mean
        # inventing an "authoritative" flag the US balance query never gave us,
        # which is precisely the confusion the three-state contract exists to
        # prevent. Callers that need it on US must fix the trader, not the wrapper.
        method = getattr(self._trader, "get_holding_quantity_checked", None)
        if method is None:
            raise BrokerUnsupported(
                f"{type(self._trader).__name__} ({self.name}/{self.market}) has no "
                "get_holding_quantity_checked; a real zero cannot be told from a "
                "failed balance query"
            )
        return method(*args, **kwargs)

    def calculate_buy_quantity(self, *args: Any, **kwargs: Any) -> int:
        return self._trader.calculate_buy_quantity(*args, **kwargs)


def kis_domestic(trader: Any) -> KisBroker:
    """Wrap a domestic KIS trader."""
    return KisBroker(trader, market="KR")


def kis_us(trader: Any) -> KisBroker:
    """Wrap a US KIS trader."""
    return KisBroker(trader, market="US")
