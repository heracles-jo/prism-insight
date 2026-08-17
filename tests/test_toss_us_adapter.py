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


def make_us_broker(responses=None):
    from trading.brokers.toss.adapter import TossBroker

    client = StubClient(responses or {})
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
