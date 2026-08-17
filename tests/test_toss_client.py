"""Toss client: what gets retried, what does not, and what a 401 must not do.

The riskiest behaviours here are the ones that look like ordinary robustness.
Retrying a POST is how a duplicate order gets placed. Looping on 401 refresh is
how two processes revoke each other's tokens forever. Both are asserted
explicitly rather than left to review.
"""

import pytest

from trading.brokers.toss import ratelimit


@pytest.fixture(autouse=True)
def _fresh_buckets():
    """Buckets are process-wide, so one test must not throttle the next."""
    ratelimit.reset_buckets()
    yield
    ratelimit.reset_buckets()


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class RecordingSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def request(self, method, url, params=None, json=None, headers=None, timeout=None):
        self.requests.append(
            {
                "method": method,
                "url": url,
                "params": params,
                "json": json,
                "headers": headers or {},
                "timeout": timeout,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse(200, {"result": {"ok": True}})


class StubAuth:
    """Stands in for TossAuth, counting refreshes."""

    base_url = "https://openapi.tossinvest.com"

    def __init__(self):
        self.token = "tok-1"
        self.invalidations = 0
        self.forced_refreshes = 0

    def auth_header(self):
        return {"Authorization": f"Bearer {self.token}"}

    def invalidate(self):
        self.invalidations += 1

    def access_token(self, *, force_refresh=False):
        if force_refresh:
            self.forced_refreshes += 1
            self.token = f"tok-{self.forced_refreshes + 1}"
        return self.token


def make_client(responses, **kwargs):
    from trading.brokers.toss.client import TossClient

    auth = StubAuth()
    session = RecordingSession(responses)
    kwargs.setdefault("sleep", lambda _: None)
    client = TossClient(auth, session=session, **kwargs)
    return client, session, auth


# ── Happy path ───────────────────────────────────────────────────────────────


def test_the_envelope_result_is_unwrapped():
    client, _, _ = make_client([FakeResponse(200, {"result": [{"symbol": "005930"}]})])
    assert client.get("/api/v1/prices") == [{"symbol": "005930"}]


def test_a_body_without_a_result_key_is_returned_whole():
    client, _, _ = make_client([FakeResponse(200, {"access_token": "x"})])
    assert client.get("/oauth2/introspect") == {"access_token": "x"}


def test_the_bearer_token_is_attached():
    client, session, _ = make_client([FakeResponse(200, {"result": {}})])
    client.get("/api/v1/accounts")
    assert session.requests[0]["headers"]["Authorization"] == "Bearer tok-1"


def test_the_account_header_is_sent_only_when_required():
    client, session, _ = make_client(
        [FakeResponse(200, {"result": {}}), FakeResponse(200, {"result": {}})],
        account_seq="acc-42",
    )
    client.get("/api/v1/prices")
    client.get("/api/v1/holdings", needs_account=True)

    assert "X-Tossinvest-Account" not in session.requests[0]["headers"]
    assert session.requests[1]["headers"]["X-Tossinvest-Account"] == "acc-42"


def test_an_account_endpoint_without_a_configured_account_fails_early():
    from trading.brokers.base import BrokerUnavailable

    client, session, _ = make_client([FakeResponse(200, {"result": {}})])
    with pytest.raises(BrokerUnavailable):
        client.get("/api/v1/holdings", needs_account=True)
    assert session.requests == [], "request left the process without an account header"


# ── 401 handling ─────────────────────────────────────────────────────────────


def test_a_401_refreshes_the_token_once_and_retries():
    client, session, auth = make_client(
        [FakeResponse(401, {"error": {"requestId": "r1", "code": "unauthorized", "message": ""}}),
         FakeResponse(200, {"result": {"ok": True}})]
    )

    assert client.get("/api/v1/accounts") == {"ok": True}
    assert auth.invalidations == 1
    assert auth.forced_refreshes == 1
    assert session.requests[1]["headers"]["Authorization"] == "Bearer tok-2"


def test_a_persistent_401_does_not_loop_refreshing():
    """Toss keeps one token per client; looping here revokes other callers."""
    from trading.brokers.toss.errors import TossAuthError

    unauthorized = {"error": {"requestId": "r", "code": "unauthorized", "message": ""}}
    client, _, auth = make_client([FakeResponse(401, unauthorized) for _ in range(5)])

    with pytest.raises(TossAuthError):
        client.get("/api/v1/accounts")
    assert auth.forced_refreshes == 1, "refresh loop would revoke tokens indefinitely"


# ── Retry policy ─────────────────────────────────────────────────────────────


def test_a_429_is_retried_and_honours_retry_after():
    slept = []
    client, session, _ = make_client(
        [FakeResponse(429, {"error": {"requestId": "r", "code": "rate-limit-exceeded", "message": ""}},
                      headers={"Retry-After": "2"}),
         FakeResponse(200, {"result": {"ok": True}})],
        sleep=slept.append,
    )

    assert client.get("/api/v1/prices") == {"ok": True}
    assert len(session.requests) == 2
    assert 2.0 <= slept[0] <= 2.5, f"ignored Retry-After: slept {slept}"


def test_backoff_is_jittered_so_accounts_do_not_retry_in_lockstep():
    delays = set()
    for _ in range(12):
        slept = []
        client, _, _ = make_client(
            [FakeResponse(503, None), FakeResponse(200, {"result": {}})],
            sleep=slept.append,
        )
        client.get("/api/v1/prices")
        delays.add(round(slept[0], 6))

    assert len(delays) > 1, "identical backoff would rebuild the burst that caused the 429"


def test_a_5xx_is_retried_up_to_the_attempt_limit():
    from trading.brokers.toss.errors import TossApiError

    client, session, _ = make_client([FakeResponse(503, None) for _ in range(5)], max_attempts=3)

    with pytest.raises(TossApiError):
        client.get("/api/v1/prices")
    assert len(session.requests) == 3


def test_a_business_refusal_is_never_retried():
    """422 order-hours-closed is an answer, not a hiccup."""
    from trading.brokers.toss.errors import TossApiError

    client, session, _ = make_client(
        [FakeResponse(422, {"error": {"requestId": "r", "code": "order-hours-closed",
                                      "message": "장 운영시간이 아닙니다."}})]
    )

    with pytest.raises(TossApiError) as excinfo:
        client.get("/api/v1/prices")

    assert excinfo.value.code == "order-hours-closed"
    assert len(session.requests) == 1, "retrying wastes the allowance the next order needs"


def test_a_post_is_not_retried_by_default():
    """A timed-out order may have been accepted; resending risks a duplicate."""
    import requests

    from trading.brokers.base import BrokerUnavailable

    class FlakySession(RecordingSession):
        def request(self, *args, **kwargs):
            self.requests.append(kwargs)
            raise requests.Timeout("timed out")

    from trading.brokers.toss.client import TossClient

    session = FlakySession([])
    client = TossClient(StubAuth(), session=session, sleep=lambda _: None)

    with pytest.raises(BrokerUnavailable):
        client.post("/api/v1/orders", json_body={"symbol": "005930"})
    assert len(session.requests) == 1, "an order was resent after an ambiguous failure"


def test_a_post_can_opt_into_retry_when_idempotent():
    client, session, _ = make_client(
        [FakeResponse(503, None), FakeResponse(200, {"result": {"orderId": "1"}})]
    )

    result = client.request(
        "POST", "/api/v1/orders", json_body={"clientOrderId": "abc"}, idempotent=True
    )
    assert result == {"orderId": "1"}
    assert len(session.requests) == 2


def test_a_network_error_becomes_broker_unavailable():
    import requests

    from trading.brokers.base import BrokerUnavailable

    class DeadSession(RecordingSession):
        def request(self, *args, **kwargs):
            self.requests.append(kwargs)
            raise requests.ConnectionError("no route")

    from trading.brokers.toss.client import TossClient

    client = TossClient(StubAuth(), session=DeadSession([]), sleep=lambda _: None)
    with pytest.raises(BrokerUnavailable):
        client.get("/api/v1/prices")


def test_a_non_json_error_body_does_not_crash_the_parser():
    """A proxy or maintenance page follows neither documented schema."""
    from trading.brokers.toss.errors import TossApiError

    client, _, _ = make_client([FakeResponse(500, None) for _ in range(3)], max_attempts=1)
    with pytest.raises(TossApiError) as excinfo:
        client.get("/api/v1/prices")
    assert excinfo.value.status == 500
