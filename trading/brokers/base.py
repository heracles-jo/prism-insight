"""What a broker has to offer, and how a caller may rely on it.

The port is written from what the trading loop actually asks for, not from any
one broker's API. That is deliberate, and it is the whole point of this file:
if the contract were shaped like KIS then adding Toss would mean bending Toss
into TR ids and `ACNT_PRDT_CD`, and the second broker would cost as much as the
first. Define the port by the need and each broker becomes an adapter.

Two things here are not stylistic and must not be smoothed away.

The first is that "this broker cannot do that" and "this broker could not do it
just now" are different exceptions. Toss has no time-based reserved order at
all, while a Toss call can also fail because a token expired. Collapsing the
two would let a permanent gap look like a transient blip and get retried
forever.

The second is that order methods return a dict rather than raising. Callers
already read `success` / `outcome_unknown` off that dict — `ExecutionService`
classifies orders by exactly those keys — so an adapter that raises on a
rejected order would be recorded as an unknown outcome and block the position.
Business rejection is a value; only infrastructure failure is an exception.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable


class BrokerError(Exception):
    """Base for every broker-layer failure."""


class BrokerUnsupported(BrokerError):
    """This broker does not offer this capability at all.

    Distinct from failure: there is nothing to retry and nothing is wrong with
    the broker. Toss publishes no time-based reserved order, so its adapter
    raises this from `buy_reserved_order` rather than returning a failure dict
    that a caller would reasonably retry.

    Deliberately not reused from `cores.market_data.source.Unsupported`: there
    an unsupported capability means "ask the next source", which is a sensible
    thing to do for a price. There is no next source for an order.
    """


class BrokerUnavailable(BrokerError):
    """This broker could not answer right now.

    Auth, network, rate limit, maintenance — all the same to a caller, which
    must decide between retrying and standing down. The message is kept for the
    log because a failure that reports only "request failed" makes an outage
    take longer to read.
    """


class OrderOutcome(TypedDict):
    """The shape every order method returns.

    Documented as a TypedDict so the contract is greppable, not to enforce it at
    runtime — the existing KIS methods build plain dicts and this must stay
    behaviour-preserving. Mirrors `DomesticStockTrading.async_buy_stock`.
    """

    success: bool
    stock_code: str
    current_price: float
    quantity: int
    total_amount: float
    order_no: str | None
    message: str
    timestamp: str

    # Set when the broker may have accepted the order despite the failure —
    # a timeout or an exception mid-flight. A caller must not record rejection.
    outcome_unknown: NotRequired[bool]


# Holding state is a three-valued answer, never a bare integer. "FLAT" means the
# broker said zero; "UNKNOWN" means the broker did not say. Selling on a zero
# that was really a failed balance query is the mistake this prevents.
#
# The quantity is `Decimal` as well as `int` because a Toss US account can hold
# a fraction of a share, and truncating that to an integer turns a real position
# into "you have none". `float` is deliberately excluded: 0.1 + 0.2 drift would
# leave a sell-everything order short by a sliver, so the position never closes.
HoldingState = tuple[str, int | Decimal | None]


@runtime_checkable
class BrokerPort(Protocol):
    """One broker, for one market.

    An implementation raises `BrokerUnsupported` for capabilities it lacks and
    `BrokerUnavailable` when a call cannot complete. Order methods return an
    `OrderOutcome` dict for business outcomes including rejection.

    The port is per-market rather than per-broker because the two do not map
    one-to-one: KIS splits domestic and overseas across separate modules, while
    Toss serves both from one API. Binding an instance to a market lets either
    shape satisfy this contract without the other paying for it.
    """

    name: str
    """Broker identifier, lowercase. e.g. `"kis"`, `"toss"`."""

    market: str
    """`"KR"` or `"US"`. Which market this instance trades."""

    # ── Orders ────────────────────────────────────────────────────────────────
    # First argument is the instrument and is passed positionally: KIS names it
    # `stock_code` domestically and `ticker` for US, and US additionally takes
    # `exchange`. Remaining arguments stay open so an adapter can absorb its
    # own broker's spelling instead of forcing one vocabulary on both.

    async def async_buy_stock(self, symbol: str, /, *args: Any, **kwargs: Any) -> OrderOutcome:
        """Buy. Returns an outcome dict; does not raise on rejection."""

    async def async_sell_stock(self, symbol: str, /, *args: Any, **kwargs: Any) -> OrderOutcome:
        """Sell. Returns an outcome dict; does not raise on rejection."""

    def amend_order(self, symbol: str, /, *args: Any, **kwargs: Any) -> OrderOutcome:
        """Amend a resting order."""

    def cancel_order(self, symbol: str, /, *args: Any, **kwargs: Any) -> OrderOutcome:
        """Cancel a resting order."""

    def buy_reserved_order(self, symbol: str, /, *args: Any, **kwargs: Any) -> OrderOutcome:
        """Queue a buy for the next session. `BrokerUnsupported` if absent."""

    def sell_reserved_order(self, symbol: str, /, *args: Any, **kwargs: Any) -> OrderOutcome:
        """Queue a sell for the next session. `BrokerUnsupported` if absent."""

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_current_price(self, symbol: str, /, *args: Any, **kwargs: Any) -> dict[str, Any] | None:
        """Last price and related fields, or None when unavailable."""

    def get_portfolio(self) -> list[dict[str, Any]]:
        """Held positions. Empty list means flat *or* unreadable — use
        `get_holding_quantity_checked` when the difference matters."""

    def get_account_summary(self) -> dict[str, Any]:
        """Cash and valuation totals. `{}` when the query fails."""

    def get_holding_quantity(self, symbol: str, /, *args: Any, **kwargs: Any) -> int | Decimal:
        """Held quantity, collapsing an unreadable balance to 0.

        May be a `Decimal` where the broker supports fractional shares. Prefer
        the checked variant, which also distinguishes "flat" from "unreadable".
        """

    def get_holding_quantity_checked(self, symbol: str, /, *args: Any, **kwargs: Any) -> HoldingState:
        """Held quantity that distinguishes a real zero from a failed query.

        May return a `Decimal` where the broker supports fractional shares
        (Toss US). Callers that can only act on whole shares must check the type
        and refuse rather than coerce — `int(Decimal("0.44"))` is 0, which reads
        as "flat" for a position that is actually held.
        """

    def calculate_buy_quantity(self, symbol: str, /, *args: Any, **kwargs: Any) -> int:
        """How many shares the configured buy amount affords."""

    # ── Session predicates ────────────────────────────────────────────────────
    # Declared on the port because a caller uses them to choose between selling
    # now and escalating to a full exit: an adapter that ships without them
    # turns that decision into an AttributeError that reads as "will queue".

    def is_market_open(self, *args: Any, **kwargs: Any) -> bool:
        """True while an order placed right now can be accepted."""

    def is_reserved_order_available(self, *args: Any, **kwargs: Any) -> bool:
        """True while a time-based reserved order can be queued.

        Always False for a broker with no reserved-order mechanism (Toss)."""
