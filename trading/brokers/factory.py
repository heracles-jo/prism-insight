"""Building the configured broker, in the one place that decides.

`ExecutionService` used to import `AsyncTradingContext` and construct it
directly. That import moves here so there is a single point where "which
broker" is answered, and the thirty-odd call sites above `ExecutionService`
never learn that the question exists.

When the broker is KIS this returns exactly the object the old code returned.
Not an equivalent one — the same construction, so the KIS path cannot regress
through a wrapper's behaviour.

Imports are deliberately inside the functions. `trading.domestic_stock_trading`
reads `kis_devlp.yaml` at import time, so importing it at module scope would
make a Toss-only installation require KIS configuration to start.
"""

from __future__ import annotations

import logging
from typing import Any

from trading.brokers import settings as config

logger = logging.getLogger(__name__)


class TossTradingContext:
    """Async context yielding a ready `TossBroker`.

    Mirrors `AsyncTradingContext`: `__aenter__` builds the trader and returns
    it, and `__aexit__` logs rather than swallowing.

    In demo mode the transport is the dry-run simulator. That substitution
    happens here, below the adapter, so the adapter runs its real code path and
    the caller cannot accidentally hold a live client while believing otherwise.
    """

    def __init__(
        self,
        *,
        market: str = "KR",
        account_name: str | None = None,
        mode: str | None = None,
        buy_amount: int | None = None,
    ):
        self.market = market.upper()
        self.account_name = account_name
        self.mode = (mode or config.trading_mode()).lower()
        self.buy_amount = buy_amount
        self.trader: Any = None

    async def __aenter__(self) -> Any:
        from trading.brokers.toss.adapter import TossBroker
        from trading.brokers.toss.auth import TossAuth, TossCredentials
        from trading.brokers.toss.client import TossClient

        settings = config.load_toss_config()
        credentials = TossCredentials(
            settings["client_id"],
            settings["client_secret"],
            base_url=settings.get("base_url") or "https://openapi.tossinvest.com",
        )
        client: Any = TossClient(
            TossAuth(credentials), account_seq=settings.get("account_seq")
        )

        if self.mode == config.DEMO:
            from trading.brokers.toss.dryrun import DryRunTossClient

            client = DryRunTossClient(client)
            logger.warning(
                "[BROKER] toss/%s running in demo — orders are simulated, not placed",
                self.market,
            )
        else:
            # Toss has no paper environment, so `real` is unambiguous and worth
            # saying out loud once per run.
            logger.warning(
                "[BROKER] toss/%s running in REAL mode — orders use real money",
                self.market,
            )

        amount = self.buy_amount
        if amount is None:
            amount = config.toss_buy_amount(self.market)

        self.trader = TossBroker(
            client,
            market=self.market,
            **({"buy_amount": amount} if amount is not None else {}),
        )
        return self.trader

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            logger.error(f"TossTradingContext error: {exc_type.__name__}: {exc_val}")


def domestic_context(account_name: str | None = None, **kwargs: Any) -> Any:
    """The KR trading context for the configured broker."""
    broker = config.selected_broker()
    if broker == config.TOSS:
        return TossTradingContext(market="KR", account_name=account_name, **kwargs)

    from trading.domestic_stock_trading import AsyncTradingContext

    return AsyncTradingContext(account_name=account_name)


def us_context(account_name: str | None = None, **kwargs: Any) -> Any:
    """The US trading context for the configured broker."""
    broker = config.selected_broker()
    if broker == config.TOSS:
        return TossTradingContext(market="US", account_name=account_name, **kwargs)

    return _kis_us_context(account_name)


def _kis_us_context(account_name: str | None) -> Any:
    """Import the US KIS context, tolerating the two package layouts.

    Preserved verbatim from `ExecutionService.us`: some long-lived processes
    import the root `trading` package before switching to the US runtime, and
    Python then caches it and cannot discover `prism-us/trading` as a
    subpackage.
    """
    import sys
    from pathlib import Path

    try:
        from trading.us_stock_trading import AsyncUSTradingContext
    except ModuleNotFoundError as exc:
        if exc.name != "trading.us_stock_trading":
            raise
        us_trading_dir = Path(__file__).resolve().parents[2] / "prism-us" / "trading"
        if not us_trading_dir.is_dir():
            raise
        path = str(us_trading_dir)
        if path not in sys.path:
            sys.path.insert(0, path)
        from us_stock_trading import AsyncUSTradingContext

    return AsyncUSTradingContext(account_name=account_name)


def broker_label(trader: Any) -> str:
    """Ledger label for whichever broker produced a result.

    Falls back to `KIS` when the object has no `name`, which is what the KIS
    traders are — they predate the port and were never given one. That keeps
    existing `broker_orders` rows consistent with new ones.
    """
    name = getattr(trader, "name", None)
    return str(name).upper() if name else "KIS"
