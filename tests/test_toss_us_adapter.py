"""Toss US: session gating, and the precision that KR never exercised.

The interesting discovery behind these tests is that Toss runs four US
sessions and publishes all of them in KST, including a day market at
09:00-16:50 KST. The usual assumption — that the US market is shut while a
Korean batch runs — does not hold here, so the morning batch can trade US
names after all. Coverage is roughly 22 hours, with a gap only between 07:00
and 09:00 KST.

The other emphasis is money precision. KR trades in whole won and the KR tests
never noticed that prices were being coerced to int; on US that coercion turns
$185.50 into $185 on every order.
"""

import asyncio
import datetime
from zoneinfo import ZoneInfo

import pytest

from tests.test_broker_contract import assert_satisfies_broker_port

KST = ZoneInfo("Asia/Seoul")


def kst(hour, minute=0, day=17):
    return datetime.datetime(2026, 8, day, hour, minute, tzinfo=KST)


CALENDAR = {
    "today": {
        "date": "2026-08-17",
        "dayMarket": {"startTime": "2026-08-17T09:00:00+09:00",
                      "endTime": "2026-08-17T16:50:00+09:00"},
        "preMarket": {"startTime": "2026-08-17T17:00:00+09:00",
                      "endTime": "2026-08-17T22:30:00+09:00"},
        "regularMarket": {"startTime": "2026-08-17T22:30:00+09:00",
                          "endTime": "2026-08-18T05:00:00+09:00"},
        "afterMarket": {"startTime": "2026-08-18T05:00:00+09:00",
                        "endTime": "2026-08-18T07:00:00+09:00"},
    },
    "previousBusinessDay": {"date": "2026-08-16"},
    "nextBusinessDay": {"date": "2026-08-18"},
}

HOLIDAY = {
    "today": {"date": "2026-08-17", "dayMarket": None, "preMarket": None,
              "regularMarket": None, "afterMarket": None},
    "previousBusinessDay": {"date": "2026-08-14"},
    "nextBusinessDay": {"date": "2026-08-18"},
}

PRICE_AAPL = [{"symbol": "AAPL", "name": "애플", "lastPrice": "185.5",
               "changeRate": "0.01", "volume": "1000"}]


def us_order(order_id="ord-us", status="FILLED", quantity="5", price="185.5"):
    return {
        "orderId": order_id, "symbol": "AAPL", "side": "BUY", "orderType": "LIMIT",
        "timeInForce": "DAY", "status": status, "price": price, "quantity": quantity,
        "currency": "USD", "orderedAt": "2026-08-17T23:30:00+09:00", "canceledAt": None,
        "execution": {"filledQuantity": quantity, "averageFilledPrice": price,
                      "filledAmount": "927.5", "commission": "0", "tax": "0",
                      "filledAt": "2026-08-17T23:30:01+09:00", "settlementDate": None},
    }


class StubClient:
    account_seq = "acc-1"

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, *, params=None, json_body=None, **kwargs):
        self.calls.append((method, path, params, json_body, kwargs))
        key = (method, path)
        if key in self.responses:
            value = self.responses[key]
            if isinstance(value, Exception):
                raise value
            return value
        raise AssertionError(f"unexpected call: {method} {path}")

    @property
    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


US_STOCK_INFO = [{"symbol": "AAPL", "name": "애플", "market": "NASDAQ"},
                 {"symbol": "JEPI", "name": "JEPI", "market": "NYSE"}]


def make_us_broker(responses=None):
    from trading.brokers.toss.adapter import TossBroker

    responses = {("GET", "/api/v1/stocks"): US_STOCK_INFO, **(responses or {})}
    client = StubClient(responses)
    return TossBroker(client, market="US", buy_amount=1000), client


TRADING = {
    ("GET", "/api/v1/market-calendar/US"): CALENDAR,
    ("GET", "/api/v1/prices"): PRICE_AAPL,
    ("POST", "/api/v1/orders"): {"orderId": "ord-us"},
    ("GET", "/api/v1/orders/ord-us"): us_order(),
}


# ── The contract still holds for US ──────────────────────────────────────────


def test_the_us_broker_satisfies_the_port():
    broker, _ = make_us_broker()
    assert_satisfies_broker_port(broker)
    assert broker.market == "US"
    assert broker.currency == "USD"


def test_the_us_factory_binds_the_market():
    from trading.brokers.toss.adapter import toss_us

    assert toss_us(object()).market == "US"


# ── Session detection ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "moment, expected",
    [
        (kst(9, 0), "dayMarket"),
        (kst(12, 0), "dayMarket"),
        (kst(16, 49), "dayMarket"),
        (kst(16, 55), None),          # between day and pre
        (kst(17, 0), "preMarket"),
        (kst(22, 0), "preMarket"),
        (kst(22, 30), "regularMarket"),
        (kst(23, 59), "regularMarket"),
        (kst(4, 0, day=18), "regularMarket"),
        (kst(5, 0, day=18), "afterMarket"),
        (kst(6, 59, day=18), "afterMarket"),
        (kst(7, 30, day=18), None),   # the one real gap
        (kst(8, 59), None),
    ],
)
def test_the_four_us_sessions_are_recognised(moment, expected):
    broker, _ = make_us_broker({("GET", "/api/v1/market-calendar/US"): CALENDAR})
    assert broker.open_us_session(now=moment) == expected


def test_a_korean_morning_batch_finds_the_us_day_market_open():
    """The premise correction: 09:00 KST is not 'US market closed' on Toss."""
    broker, _ = make_us_broker({("GET", "/api/v1/market-calendar/US"): CALENDAR})
    assert broker.open_us_session(now=kst(9, 5)) == "dayMarket"


def test_a_holiday_has_no_open_session():
    broker, _ = make_us_broker({("GET", "/api/v1/market-calendar/US"): HOLIDAY})
    assert broker.open_us_session(now=kst(23, 0)) is None


def test_an_unavailable_calendar_reads_as_closed():
    """Refusing beats ordering into a session that may not exist."""
    from trading.brokers.base import BrokerUnavailable

    broker, _ = make_us_broker(
        {("GET", "/api/v1/market-calendar/US"): BrokerUnavailable("down")}
    )
    assert broker.open_us_session(now=kst(23, 0)) is None


def test_a_malformed_calendar_reads_as_closed():
    broker, _ = make_us_broker({("GET", "/api/v1/market-calendar/US"): {"today": "nope"}})
    assert broker.open_us_session(now=kst(23, 0)) is None


# ── Gating orders ────────────────────────────────────────────────────────────


def test_an_order_placed_during_a_session_goes_through():
    broker, client = make_us_broker(TRADING)

    result = asyncio.run(broker.async_buy_stock("AAPL"))
    assert result["success"] is True
    assert len(client.posts) == 1


def test_an_order_outside_every_session_is_refused_without_reaching_toss():
    broker, client = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): HOLIDAY,
        ("GET", "/api/v1/prices"): PRICE_AAPL,
    })

    result = asyncio.run(broker.async_buy_stock("AAPL"))
    assert result["success"] is False
    assert client.posts == [], "an order was sent while every session was shut"


def test_a_closed_market_is_a_definite_failure_not_an_unknown_one():
    """Raising here would reach ExecutionService and be recorded UNKNOWN,
    blocking a position over an order that provably never left the process."""
    broker, _ = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): HOLIDAY,
        ("GET", "/api/v1/prices"): PRICE_AAPL,
    })

    result = asyncio.run(broker.async_buy_stock("AAPL"))
    assert "outcome_unknown" not in result


def test_the_refusal_explains_that_there_is_nothing_to_queue_into():
    broker, _ = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): HOLIDAY,
        ("GET", "/api/v1/prices"): PRICE_AAPL,
    })

    message = asyncio.run(broker.async_buy_stock("AAPL"))["message"]
    assert "no US session open" in message
    assert "reserved order" in message


def test_selling_is_gated_too():
    broker, client = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): HOLIDAY,
        ("GET", "/api/v1/prices"): PRICE_AAPL,
        ("GET", "/api/v1/holdings"): {
            "items": [{"symbol": "AAPL", "name": "애플", "currency": "USD",
                       "quantity": "5", "lastPrice": "185.5",
                       "averagePurchasePrice": "180",
                       "marketValue": {"amount": "927.5"},
                       "profitLoss": {"amount": "27.5", "rate": "0.0305"}}]
        },
    })

    result = asyncio.run(broker.async_sell_stock("AAPL"))
    assert result["success"] is False
    assert client.posts == []


def test_kr_orders_are_not_gated_by_the_us_calendar():
    """KR must not acquire a US dependency."""
    from trading.brokers.toss.adapter import TossBroker

    client = StubClient({
        ("GET", "/api/v1/stocks"): [{"symbol": "005930", "name": "삼성전자"}],
        ("GET", "/api/v1/prices"): [{"symbol": "005930", "name": "삼성전자",
                                     "lastPrice": "70000", "changeRate": "0",
                                     "volume": "1"}],
        ("POST", "/api/v1/orders"): {"orderId": "ord-kr"},
        ("GET", "/api/v1/orders/ord-kr"): {
            "orderId": "ord-kr", "symbol": "005930", "side": "BUY",
            "orderType": "LIMIT", "timeInForce": "DAY", "status": "FILLED",
            "price": "70000", "quantity": "1", "currency": "KRW",
            "orderedAt": "x", "canceledAt": None,
            "execution": {"filledQuantity": "1", "averageFilledPrice": "70000",
                          "filledAmount": "70000", "commission": "0", "tax": "0",
                          "filledAt": "x", "settlementDate": None},
        },
    })
    broker = TossBroker(client, market="KR", buy_amount=100000)

    assert asyncio.run(broker.async_buy_stock("005930"))["success"] is True
    assert not any("market-calendar" in c[1] for c in client.calls)


# ── Money precision ──────────────────────────────────────────────────────────


def test_us_limit_prices_keep_their_cents():
    """int() coercion would send $185 for a $185.50 limit on every order."""
    broker, client = make_us_broker(TRADING)

    asyncio.run(broker.async_buy_stock("AAPL", limit_price=185.5))
    assert client.posts[0][3]["price"] == "185.5"


def test_the_default_limit_keeps_the_cents_of_the_market_price():
    broker, client = make_us_broker(TRADING)

    asyncio.run(broker.async_buy_stock("AAPL"))
    assert client.posts[0][3]["price"] == "185.5"


@pytest.mark.parametrize(
    "price, expected",
    [
        (185.5, "185.5"),
        (185.567, "185.56"),      # >= $1 → two places, truncated not rounded
        (185.999, "185.99"),
        (0.12345, "0.1234"),      # < $1 → four places, truncated
        (0.9999, "0.9999"),
    ],
)
def test_us_prices_follow_the_published_precision_rules(price, expected):
    """Toss truncates rather than rounds; rounding up can be refused outright."""
    broker, _ = make_us_broker()
    assert broker._format_price(price) == expected


def test_kr_prices_stay_whole_won():
    from trading.brokers.toss.adapter import TossBroker

    broker = TossBroker(StubClient({}), market="KR")
    assert broker._format_price(70000) == "70000"
    assert broker._format_price(70000.9) == "70000"


# ── US portfolio filtering ───────────────────────────────────────────────────


def test_a_us_broker_ignores_krw_holdings():
    """One Toss account holds both markets; each broker sees only its own."""
    broker, _ = make_us_broker({
        ("GET", "/api/v1/holdings"): {
            "items": [
                {"symbol": "005930", "name": "삼성전자", "currency": "KRW",
                 "quantity": "10", "lastPrice": "72000", "averagePurchasePrice": "65000",
                 "marketValue": {"amount": "720000"},
                 "profitLoss": {"amount": "70000", "rate": "0.1077"}},
                {"symbol": "AAPL", "name": "애플", "currency": "USD",
                 "quantity": "5", "lastPrice": "185.5", "averagePurchasePrice": "180",
                 "marketValue": {"amount": "927.5"},
                 "profitLoss": {"amount": "27.5", "rate": "0.0305"}},
            ]
        },
    })

    symbols = [row["stock_code"] for row in broker.get_portfolio()]
    assert symbols == ["AAPL"]


def test_a_us_holding_reports_held():
    broker, _ = make_us_broker({
        ("GET", "/api/v1/holdings"): {
            "items": [{"symbol": "AAPL", "name": "애플", "currency": "USD",
                       "quantity": "5", "lastPrice": "185.5",
                       "averagePurchasePrice": "180",
                       "marketValue": {"amount": "927.5"},
                       "profitLoss": {"amount": "27.5", "rate": "0.0305"}}]
        },
    })

    assert broker.get_holding_quantity_checked("AAPL") == ("HELD", 5)


def test_us_prices_stay_fractional():
    """KR rounds to int; doing that on US would lose the cents in every report."""
    broker, _ = make_us_broker({("GET", "/api/v1/prices"): PRICE_AAPL})

    assert broker.get_current_price("AAPL")["current_price"] == 185.5


# ── Reserved orders remain permanently unsupported ───────────────────────────


def test_us_reserved_orders_still_raise_unsupported():
    """Absence of the capability is permanent; a closed session is not."""
    from trading.brokers.base import BrokerUnsupported

    broker, _ = make_us_broker()
    with pytest.raises(BrokerUnsupported):
        broker.buy_reserved_order("AAPL", limit_price=185.5)


# ── Fractional holdings (PRD Phase 2) ────────────────────────────────────────


FRACTIONAL_HOLDINGS = {
    "items": [
        {"symbol": "JEPI", "name": "JEPI", "currency": "USD", "quantity": "0.44519",
         "lastPrice": "58.2", "averagePurchasePrice": "57.0",
         "marketValue": {"amount": "25.91"}, "profitLoss": {"amount": "0.53", "rate": "0.0210"}},
        {"symbol": "TQQQ", "name": "TQQQ", "currency": "USD", "quantity": "1.68024",
         "lastPrice": "72.0", "averagePurchasePrice": "68.4",
         "marketValue": {"amount": "120.98"}, "profitLoss": {"amount": "6.05", "rate": "0.0526"}},
        {"symbol": "005930", "name": "삼성전자", "currency": "KRW", "quantity": "2",
         "lastPrice": "274500", "averagePurchasePrice": "270000",
         "marketValue": {"amount": "549000"}, "profitLoss": {"amount": "9000", "rate": "0.0166"}},
    ]
}


def test_a_sub_share_holding_is_not_reported_as_flat():
    """The bug this phase exists to remove: 0.44519 shares read as ("FLAT", 0)."""
    from decimal import Decimal

    broker, _ = make_us_broker({("GET", "/api/v1/holdings"): FRACTIONAL_HOLDINGS})

    assert broker.get_holding_quantity_checked("JEPI") == ("HELD", Decimal("0.44519"))


def test_every_fractional_holding_survives_the_portfolio():
    """Four of five real holdings used to disappear entirely."""
    broker, _ = make_us_broker({("GET", "/api/v1/holdings"): FRACTIONAL_HOLDINGS})

    symbols = [r["stock_code"] for r in broker.get_portfolio()]
    assert symbols == ["JEPI", "TQQQ"]


def test_quantities_keep_full_precision():
    """A float round-trip would lose digits and strand a sliver on a full sell."""
    from decimal import Decimal

    broker, _ = make_us_broker({("GET", "/api/v1/holdings"): FRACTIONAL_HOLDINGS})

    rows = {r["stock_code"]: r["quantity"] for r in broker.get_portfolio()}
    assert rows["TQQQ"] == Decimal("1.68024")
    assert isinstance(rows["TQQQ"], Decimal)


def test_a_fractional_holding_passes_the_production_normaliser():
    """It must survive the shared gate, not just the adapter."""
    from decimal import Decimal

    from prism_core.execution_service import normalize_checked_holding

    broker, _ = make_us_broker({("GET", "/api/v1/holdings"): FRACTIONAL_HOLDINGS})

    assert normalize_checked_holding(
        broker.get_holding_quantity_checked("JEPI")
    ) == ("HELD", Decimal("0.44519"))


def test_kr_quantities_stay_integers():
    """KR shares are always whole; the shared code path must not change type."""
    from trading.brokers.toss.adapter import TossBroker

    client = StubClient({("GET", "/api/v1/holdings"): FRACTIONAL_HOLDINGS})
    kr = TossBroker(client, market="KR")

    rows = kr.get_portfolio()
    assert [r["stock_code"] for r in rows] == ["005930"]
    assert rows[0]["quantity"] == 2
    assert type(rows[0]["quantity"]) is int
    assert kr.get_holding_quantity_checked("005930") == ("HELD", 2)


def test_an_unexpected_fractional_kr_quantity_is_logged(caplog):
    """Should be unreachable — Toss rejects domestic fractional orders — but a
    silent truncation would be worse than a line in the log."""
    import logging

    from trading.brokers.toss.adapter import TossBroker

    client = StubClient({("GET", "/api/v1/holdings"): {
        "items": [{"symbol": "005930", "name": "삼성전자", "currency": "KRW",
                   "quantity": "2.5", "lastPrice": "274500",
                   "averagePurchasePrice": "270000",
                   "marketValue": {"amount": "686250"},
                   "profitLoss": {"amount": "11250", "rate": "0.0166"}}]
    }})
    kr = TossBroker(client, market="KR")

    with caplog.at_level(logging.WARNING):
        rows = kr.get_portfolio()

    assert type(rows[0]["quantity"]) is int
    assert rows[0]["quantity"] == 2
    assert "fractional KR quantity" in caplog.text


def test_get_holding_quantity_does_not_collapse_a_fraction_to_zero():
    """Returning 0 here would recreate the "you hold nothing" bug."""
    from decimal import Decimal

    broker, _ = make_us_broker({("GET", "/api/v1/holdings"): FRACTIONAL_HOLDINGS})

    assert broker.get_holding_quantity("JEPI") == Decimal("0.44519")


def test_a_genuinely_absent_symbol_is_still_flat():
    broker, _ = make_us_broker({("GET", "/api/v1/holdings"): FRACTIONAL_HOLDINGS})

    assert broker.get_holding_quantity_checked("NVDA") == ("FLAT", 0)
    assert broker.get_holding_quantity("NVDA") == 0


# ── Fractional selling (PRD Phase 3) ─────────────────────────────────────────


def _calendar_around(now, *, opens_delta_h, closes_delta_h):
    """A calendar whose regular session brackets `now` by the given offsets."""
    import datetime as _dt

    opens = now + _dt.timedelta(hours=opens_delta_h)
    closes = now + _dt.timedelta(hours=closes_delta_h)
    return {
        "today": {
            "date": str(now.date()),
            "dayMarket": None,
            "preMarket": None,
            "regularMarket": {"startTime": opens.isoformat(), "endTime": closes.isoformat()},
            "afterMarket": None,
        },
        "previousBusinessDay": {"date": str(now.date())},
        "nextBusinessDay": {"date": str(now.date())},
    }


def _now_kst():
    import datetime as _dt

    return _dt.datetime.now(KST)


PRICE_JEPI = [{"symbol": "JEPI", "name": "JEPI", "lastPrice": "58.2",
               "changeRate": "0.01", "volume": "1000"}]


def test_the_fractional_window_closes_an_hour_before_the_session_does():
    """A position can be visible and unsellable at the same time."""
    now = _now_kst()
    broker, _ = make_us_broker({
        # Session opened 1h ago, closes in 30 minutes → inside the session but
        # past the fractional cutoff.
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=0.5
        ),
    })

    assert broker.open_us_session() == "regularMarket"
    assert broker.fractional_window_open() is False


def test_the_fractional_window_is_open_early_in_the_session():
    now = _now_kst()
    broker, _ = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=5
        ),
    })

    assert broker.fractional_window_open() is True


def test_a_fractional_sell_goes_out_as_market_without_a_price():
    """Toss takes fractional quantity on MARKET only, and rejects a price with it."""
    now = _now_kst()
    broker, client = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=5
        ),
        ("GET", "/api/v1/prices"): PRICE_JEPI,
        ("GET", "/api/v1/holdings"): FRACTIONAL_HOLDINGS,
        ("POST", "/api/v1/orders"): {"orderId": "ord-frac"},
        ("GET", "/api/v1/orders/ord-frac"): us_order(order_id="ord-frac", quantity="0.44519"),
    })

    result = asyncio.run(broker.async_sell_stock("JEPI"))

    posted = next(c[3] for c in client.calls if c[0] == "POST")
    assert posted["orderType"] == "MARKET"
    assert "price" not in posted, "a price with a market order is rejected outright"
    assert posted["quantity"] == "0.44519"
    assert posted["side"] == "SELL"
    assert result["success"] is True


def test_a_whole_share_sell_still_goes_out_as_limit():
    """The fractional path must not change ordinary selling."""
    now = _now_kst()
    broker, client = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=5
        ),
        ("GET", "/api/v1/prices"): PRICE_AAPL,
        ("GET", "/api/v1/holdings"): {"items": [
            {"symbol": "AAPL", "name": "애플", "currency": "USD", "quantity": "3",
             "lastPrice": "185.5", "averagePurchasePrice": "180",
             "marketValue": {"amount": "556.5"}, "profitLoss": {"amount": "16.5", "rate": "0.03"}}
        ]},
        ("POST", "/api/v1/orders"): {"orderId": "ord-whole"},
        ("GET", "/api/v1/orders/ord-whole"): us_order(order_id="ord-whole", quantity="3"),
    })

    asyncio.run(broker.async_sell_stock("AAPL"))

    posted = next(c[3] for c in client.calls if c[0] == "POST")
    assert posted["orderType"] == "LIMIT"
    assert posted["price"] == "185.5"


def test_a_fractional_sell_outside_the_window_fails_explicitly():
    """PRD Phase 3 success signal: refused, not queued, not silently rounded."""
    now = _now_kst()
    broker, client = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=0.5
        ),
        ("GET", "/api/v1/prices"): PRICE_JEPI,
        ("GET", "/api/v1/holdings"): FRACTIONAL_HOLDINGS,
    })

    result = asyncio.run(broker.async_sell_stock("JEPI"))

    assert result["success"] is False
    assert "outcome_unknown" not in result, "it provably never left the process"
    assert "fractional" in result["message"]
    assert not [c for c in client.calls if c[0] == "POST"]


def test_a_fractional_quantity_is_truncated_to_six_places_downward():
    """Rounding up would ask to sell more than is held."""
    from decimal import Decimal

    broker, _ = make_us_broker()

    assert broker._round_fractional(Decimal("0.4451949999")) == Decimal("0.445194")
    assert broker._round_fractional(Decimal("0.9999999")) == Decimal("0.999999")


def test_a_fractional_kr_order_is_refused_before_it_is_sent():
    """Toss rejects domestic fractional outright; say so rather than relay it."""
    from decimal import Decimal

    from trading.brokers.toss.adapter import TossBroker

    client = StubClient({})
    kr = TossBroker(client, market="KR")

    refusal = kr._refuse_fractional("005930", "SELL", Decimal("0.5"), 70000.0)
    assert refusal is not None and refusal["success"] is False
    assert "US orders only" in refusal["message"]


def test_a_fractional_buy_is_refused_and_points_at_the_amount_route():
    from decimal import Decimal

    broker, _ = make_us_broker()

    refusal = broker._refuse_fractional("AAPL", "BUY", Decimal("0.5"), 185.5)
    assert refusal is not None and refusal["success"] is False
    assert "orderAmount" in refusal["message"]


def test_an_unavailable_calendar_closes_the_fractional_window():
    """Refusing beats guessing that the window is open."""
    from trading.brokers.base import BrokerUnavailable

    broker, _ = make_us_broker(
        {("GET", "/api/v1/market-calendar/US"): BrokerUnavailable("down")}
    )
    assert broker.fractional_window_open() is False


# ── Amount-based buying (PRD Phase 4) ────────────────────────────────────────


def test_a_budget_below_one_share_buys_by_amount():
    """Previously a dead end: floor(100/185.5) is 0, so nothing was bought."""
    now = _now_kst()
    broker, client = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=5
        ),
        ("GET", "/api/v1/prices"): PRICE_AAPL,
        ("POST", "/api/v1/orders"): {"orderId": "ord-amt"},
        ("GET", "/api/v1/orders/ord-amt"): us_order(order_id="ord-amt", quantity="0.539083"),
    })
    broker.buy_amount = 100

    result = asyncio.run(broker.async_buy_stock("AAPL"))

    posted = next(c[3] for c in client.calls if c[0] == "POST")
    assert posted["orderType"] == "MARKET"
    assert posted["orderAmount"] == "100.00"
    assert "quantity" not in posted, "send exactly one of quantity or orderAmount"
    assert "price" not in posted, "a price with a market order is rejected"
    assert result["success"] is True


def test_an_affordable_whole_share_still_buys_by_quantity():
    """The amount path must not take over ordinary buying."""
    now = _now_kst()
    broker, client = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=5
        ),
        ("GET", "/api/v1/prices"): PRICE_AAPL,
        ("POST", "/api/v1/orders"): {"orderId": "ord-whole"},
        ("GET", "/api/v1/orders/ord-whole"): us_order(order_id="ord-whole", quantity="5"),
    })
    broker.buy_amount = 1000

    asyncio.run(broker.async_buy_stock("AAPL"))

    posted = next(c[3] for c in client.calls if c[0] == "POST")
    assert posted["orderType"] == "LIMIT"
    assert posted["quantity"] == "5"
    assert "orderAmount" not in posted


def test_an_amount_buy_outside_the_window_fails_with_the_reason():
    now = _now_kst()
    broker, client = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=0.5
        ),
        ("GET", "/api/v1/prices"): PRICE_AAPL,
    })
    broker.buy_amount = 100

    result = asyncio.run(broker.async_buy_stock("AAPL"))

    assert result["success"] is False
    assert "amount-based order cannot be placed now" in result["message"]
    assert not [c for c in client.calls if c[0] == "POST"]


def test_a_kr_budget_below_one_share_is_still_a_plain_refusal():
    """Toss has no amount-based route for domestic stocks."""
    from trading.brokers.toss.adapter import TossBroker

    client = StubClient({
        ("GET", "/api/v1/stocks"): [{"symbol": "005930", "name": "삼성전자"}],
        ("GET", "/api/v1/prices"): [{"symbol": "005930", "name": "삼성전자",
                                     "lastPrice": "274500"}],
    })
    kr = TossBroker(client, market="KR", buy_amount=1000)

    result = asyncio.run(kr.async_buy_stock("005930"))

    assert result["success"] is False
    assert "US stocks only" in result["message"]
    assert not [c for c in client.calls if c[0] == "POST"]


def test_the_order_amount_is_truncated_to_cents():
    now = _now_kst()
    broker, client = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=5
        ),
        ("GET", "/api/v1/prices"): PRICE_AAPL,
        ("POST", "/api/v1/orders"): {"orderId": "ord-amt"},
        ("GET", "/api/v1/orders/ord-amt"): us_order(order_id="ord-amt", quantity="0.5"),
    })

    asyncio.run(broker.async_buy_stock("AAPL", buy_amount=100.987))

    posted = next(c[3] for c in client.calls if c[0] == "POST")
    assert posted["orderAmount"] == "100.98", "rounding up could exceed the budget"


def test_the_filled_quantity_comes_from_the_read_back():
    """orderAmount fixes the money; the share count is only known after filling."""
    from decimal import Decimal

    now = _now_kst()
    broker, _ = make_us_broker({
        ("GET", "/api/v1/market-calendar/US"): _calendar_around(
            now, opens_delta_h=-1, closes_delta_h=5
        ),
        ("GET", "/api/v1/prices"): PRICE_AAPL,
        ("POST", "/api/v1/orders"): {"orderId": "ord-amt"},
        ("GET", "/api/v1/orders/ord-amt"): us_order(order_id="ord-amt", quantity="0.539083"),
    })
    broker.buy_amount = 100

    result = asyncio.run(broker.async_buy_stock("AAPL"))
    assert result["quantity"] == Decimal("0.539083")
