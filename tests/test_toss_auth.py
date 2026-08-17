"""Toss auth: the token is shared and singular, so caching is correctness.

Toss keeps one valid token per client and revokes the previous one on reissue.
These tests therefore assert on *how many times* the token endpoint was called,
not just on the token that came back — an implementation that works but fetches
twice has broken the other caller.
"""

import json
import threading
import time

import pytest


class FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Counts token issues, because that count is the thing under test."""

    def __init__(self, responses=None):
        self.post_calls = []
        self._responses = list(responses or [])

    def post(self, url, data=None, headers=None, timeout=None):
        self.post_calls.append({"url": url, "data": data, "timeout": timeout})
        if self._responses:
            return self._responses.pop(0)
        return FakeResponse(
            200, {"access_token": f"tok-{len(self.post_calls)}", "token_type": "Bearer", "expires_in": 86400}
        )


def make_auth(tmp_path, session=None, **kwargs):
    from trading.brokers.toss.auth import TossAuth, TossCredentials

    return TossAuth(
        TossCredentials("c_test_client", "s_test_secret"),
        config_dir=tmp_path,
        session=session or FakeSession(),
        **kwargs,
    )


# ── Credentials ──────────────────────────────────────────────────────────────


def test_credentials_require_both_halves():
    from trading.brokers.toss.auth import TossCredentials

    with pytest.raises(ValueError):
        TossCredentials("", "secret")
    with pytest.raises(ValueError):
        TossCredentials("client", "")


def test_cache_key_does_not_leak_the_client_id():
    from trading.brokers.toss.auth import TossCredentials

    creds = TossCredentials("c_01HXYZABCDEFG123456789", "s_secret")
    assert creds.client_id not in creds.cache_key
    assert len(creds.cache_key) == 16


def test_repr_does_not_leak_the_secret():
    from trading.brokers.toss.auth import TossCredentials

    creds = TossCredentials("c_01HXYZABCDEFG123456789", "s_super_secret_value")
    assert "s_super_secret_value" not in repr(creds)


# ── Issuing and caching ──────────────────────────────────────────────────────


def test_token_is_issued_once_and_then_reused(tmp_path):
    session = FakeSession()
    auth = make_auth(tmp_path, session)

    assert auth.access_token() == "tok-1"
    assert auth.access_token() == "tok-1"
    assert auth.access_token() == "tok-1"

    assert len(session.post_calls) == 1, "a reissue would revoke the live token"


def test_token_request_uses_form_encoded_client_credentials(tmp_path):
    session = FakeSession()
    make_auth(tmp_path, session).access_token()

    sent = session.post_calls[0]
    assert sent["url"].endswith("/oauth2/token")
    assert sent["data"] == {
        "grant_type": "client_credentials",
        "client_id": "c_test_client",
        "client_secret": "s_test_secret",
    }


def test_a_second_instance_reuses_the_cached_token_from_disk(tmp_path):
    """Separate processes share one client_id, so they must share one token."""
    first_session = FakeSession()
    make_auth(tmp_path, first_session).access_token()

    second_session = FakeSession()
    token = make_auth(tmp_path, second_session).access_token()

    assert token == "tok-1"
    assert second_session.post_calls == [], "second process reissued and revoked the first token"


def test_concurrent_callers_issue_only_one_token(tmp_path):
    session = FakeSession()
    auth = make_auth(tmp_path, session)
    tokens = []
    barrier = threading.Barrier(8)

    def grab():
        barrier.wait()
        tokens.append(auth.access_token())

    threads = [threading.Thread(target=grab) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(session.post_calls) == 1, "token stampede: callers revoked each other"
    assert set(tokens) == {"tok-1"}


def test_expiry_is_shortened_by_the_renewal_margin(tmp_path):
    auth = make_auth(tmp_path, FakeSession())
    auth.access_token()

    cached = json.loads(
        next(tmp_path.glob("toss_token_*.json")).read_text(encoding="utf-8")
    )
    remaining = cached["expires_at"] - time.time()
    # 86400 minus the 300s margin, allowing for test execution time.
    assert 86000 < remaining < 86100


def test_a_missing_expires_in_falls_back_to_a_short_life(tmp_path):
    """Trusting an absent TTL as long-lived means using a dead token all day."""
    session = FakeSession([FakeResponse(200, {"access_token": "tok-x", "token_type": "Bearer"})])
    auth = make_auth(tmp_path, session)
    auth.access_token()

    cached = json.loads(
        next(tmp_path.glob("toss_token_*.json")).read_text(encoding="utf-8")
    )
    assert cached["expires_at"] - time.time() < 300


def test_an_expired_disk_token_is_not_reused(tmp_path):
    from trading.brokers.toss.auth import TossCredentials

    creds = TossCredentials("c_test_client", "s_test_secret")
    stale = tmp_path / f"toss_token_{creds.cache_key}.json"
    stale.write_text(
        json.dumps({"access_token": "expired", "expires_at": time.time() - 1}),
        encoding="utf-8",
    )

    session = FakeSession()
    assert make_auth(tmp_path, session).access_token() == "tok-1"
    assert len(session.post_calls) == 1


def test_a_corrupt_token_file_is_ignored_rather_than_fatal(tmp_path):
    from trading.brokers.toss.auth import TossCredentials

    creds = TossCredentials("c_test_client", "s_test_secret")
    (tmp_path / f"toss_token_{creds.cache_key}.json").write_text("{not json", encoding="utf-8")

    assert make_auth(tmp_path, FakeSession()).access_token() == "tok-1"


def test_the_token_file_is_not_world_readable(tmp_path):
    make_auth(tmp_path, FakeSession()).access_token()

    mode = next(tmp_path.glob("toss_token_*.json")).stat().st_mode & 0o777
    assert mode == 0o600, f"token file mode {oct(mode)} exposes a credential"


def test_force_refresh_issues_a_new_token(tmp_path):
    session = FakeSession()
    auth = make_auth(tmp_path, session)

    assert auth.access_token() == "tok-1"
    assert auth.access_token(force_refresh=True) == "tok-2"
    assert len(session.post_calls) == 2


def test_invalidate_clears_memory_and_disk(tmp_path):
    session = FakeSession()
    auth = make_auth(tmp_path, session)
    auth.access_token()
    assert list(tmp_path.glob("toss_token_*.json"))

    auth.invalidate()
    assert not list(tmp_path.glob("toss_token_*.json"))

    assert auth.access_token() == "tok-2"


def test_auth_header_is_a_bearer_header(tmp_path):
    assert make_auth(tmp_path, FakeSession()).auth_header() == {
        "Authorization": "Bearer tok-1"
    }


# ── Failure paths ────────────────────────────────────────────────────────────


def test_bad_credentials_raise_auth_error(tmp_path):
    from trading.brokers.toss.errors import TossAuthError

    session = FakeSession(
        [FakeResponse(401, {"error": "invalid_client", "error_description": "Client authentication failed."})]
    )
    with pytest.raises(TossAuthError) as excinfo:
        make_auth(tmp_path, session).access_token()

    assert excinfo.value.code == "invalid_client"
    assert excinfo.value.status == 401


def test_a_blocked_ip_says_what_to_do_about_it(tmp_path, caplog):
    """A 403 is silent about the WTS setting that caused it; we must not be."""
    import logging

    from trading.brokers.toss.errors import TossAuthError

    session = FakeSession(
        [FakeResponse(403, {"error": "access_denied", "error_description": "IP address not allowed"})]
    )
    with caplog.at_level(logging.ERROR):
        with pytest.raises(TossAuthError):
            make_auth(tmp_path, session).access_token()

    assert "허용 IP" in caplog.text


def test_a_network_failure_becomes_broker_unavailable(tmp_path):
    import requests

    from trading.brokers.base import BrokerUnavailable

    class DeadSession:
        def post(self, *args, **kwargs):
            raise requests.ConnectionError("no route to host")

    with pytest.raises(BrokerUnavailable):
        make_auth(tmp_path, DeadSession()).access_token()


def test_a_response_without_a_token_is_not_treated_as_success(tmp_path):
    from trading.brokers.base import BrokerUnavailable

    session = FakeSession([FakeResponse(200, {"token_type": "Bearer", "expires_in": 86400})])
    with pytest.raises(BrokerUnavailable):
        make_auth(tmp_path, session).access_token()


def test_an_unwritable_cache_dir_does_not_break_auth(tmp_path):
    """A cache we cannot persist is slow, not broken."""
    auth = make_auth(tmp_path / "nested" / "deeper", FakeSession())
    assert auth.access_token() == "tok-1"
