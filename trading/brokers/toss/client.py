"""The one place Toss is actually spoken to.

Everything above this file works in dicts and exceptions; everything below is
HTTP. Keeping that boundary in one class is what lets the dry-run simulator in
Phase 3 sit here and stop orders leaving the process while the rest of the code
runs unchanged.

Three behaviours are deliberate and worth stating.

Retries are only for failures that could plausibly succeed on a second attempt:
network errors, 429, and 5xx. A 422 refusing an order because the market is
closed is a decision, not a hiccup, and retrying it wastes the rate-limit
allowance that the *next* real order needs.

Retries also only apply to reads by default. A `POST /api/v1/orders` that times
out may well have been accepted, and sending it again risks a duplicate
position. Toss offers `clientOrderId` as an idempotency key precisely for this,
so an order retry is opt-in and only safe when the caller supplies one.

A 401 triggers exactly one token refresh, never a loop. Because Toss keeps a
single valid token per client, two callers each refreshing on 401 would revoke
each other's tokens indefinitely.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any

import requests

from trading.brokers.base import BrokerUnavailable
from trading.brokers.toss import ratelimit
from trading.brokers.toss.auth import TossAuth
from trading.brokers.toss.errors import (
    TossApiError,
    TossAuthError,
    TossRateLimited,
    parse_error_response,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_TIMEOUT = 10.0

# Cap a server-suggested wait. Toss suggests ~1s; anything far larger is more
# likely a broken header than real advice, and a batch must not park on it.
_MAX_RETRY_AFTER = 30.0

_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class TossClient:
    """Authenticated, rate-limited, retrying access to the Toss REST API."""

    def __init__(
        self,
        auth: TossAuth,
        *,
        account_seq: str | None = None,
        session: requests.Session | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        sleep=time.sleep,
    ):
        self._auth = auth
        self._account_seq = account_seq
        self._session = session or requests.Session()
        self._timeout = timeout
        self._max_attempts = max(1, max_attempts)
        # Injected so tests do not spend real seconds proving backoff works.
        self._sleep = sleep

    @property
    def account_seq(self) -> str | None:
        return self._account_seq

    # ── Verbs ─────────────────────────────────────────────────────────────────

    def get(self, path: str, *, params: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request("GET", path, params=params, **kwargs)

    def post(self, path: str, *, json_body: dict[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.request("POST", path, json_body=json_body, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> Any:
        return self.request("DELETE", path, **kwargs)

    # ── Core ──────────────────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        group: str = ratelimit.DEFAULT,
        needs_account: bool = False,
        idempotent: bool | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Call Toss and return the decoded `result`, or raise.

        `idempotent` decides whether a failed attempt may be repeated. It
        defaults to True for GET/DELETE and False for POST — a POST is an order
        unless the caller says otherwise, and replaying one is how duplicate
        positions happen.
        """
        if idempotent is None:
            idempotent = method.upper() in {"GET", "DELETE"}

        url = f"{self._auth.base_url}{path}"
        attempts = self._max_attempts if idempotent else 1
        refreshed = False
        last_error: Exception | None = None

        for attempt in range(1, attempts + 1):
            ratelimit.bucket_for(group).acquire()

            try:
                response = self._send(
                    method, url, params, json_body, needs_account, timeout
                )
            except requests.RequestException as exc:
                last_error = BrokerUnavailable(f"Toss request failed: {method} {path}: {exc}")
                if attempt < attempts:
                    self._backoff(attempt, None, path)
                    continue
                raise last_error from exc

            if response.status_code < 300:
                return _unwrap(response)

            error = parse_error_response(
                response.status_code, _safe_json(response), response.headers
            )

            # One refresh, then give up. Looping here would have two processes
            # revoking each other's tokens forever.
            if isinstance(error, TossAuthError) and error.status == 401 and not refreshed:
                logger.info("[TOSS] 401 on %s; refreshing token once", path)
                self._auth.invalidate()
                self._auth.access_token(force_refresh=True)
                refreshed = True
                continue

            if error.is_business_refusal:
                # Toss understood and declined. Surface it as-is.
                raise error

            if response.status_code in _RETRYABLE_STATUSES and attempt < attempts:
                retry_after = getattr(error, "retry_after", None)
                self._backoff(attempt, retry_after, path)
                last_error = error
                continue

            raise error

        raise last_error or BrokerUnavailable(f"Toss request failed: {method} {path}")

    def _send(
        self,
        method: str,
        url: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        needs_account: bool,
        timeout: float | None,
    ) -> requests.Response:
        headers = {"Accept": "application/json"}
        headers.update(self._auth.auth_header())

        if needs_account:
            if not self._account_seq:
                raise BrokerUnavailable(
                    "this endpoint requires an account but no account_seq is configured"
                )
            headers["X-Tossinvest-Account"] = self._account_seq

        return self._session.request(
            method,
            url,
            params=params,
            json=json_body,
            headers=headers,
            timeout=timeout if timeout is not None else self._timeout,
        )

    def _backoff(self, attempt: int, retry_after: float | None, path: str) -> None:
        """Wait before retrying, preferring the server's own advice.

        Jitter matters more than usual here: PRISM fans out across accounts, so
        without it every account would retry on the same tick and rebuild the
        burst that caused the 429.
        """
        if retry_after is not None:
            delay = min(retry_after, _MAX_RETRY_AFTER)
        else:
            delay = min(2.0 ** (attempt - 1), _MAX_RETRY_AFTER)
        delay += random.uniform(0, delay * 0.25)

        logger.warning(
            "[TOSS] retrying %s after %.2fs (attempt %d)", path, delay, attempt
        )
        self._sleep(delay)


def _unwrap(response: requests.Response) -> Any:
    """Return the envelope's `result`, or the whole body if it has none.

    Toss wraps successful responses in `{"result": ...}`, but `/oauth2/token`
    documents itself as an exception, and an unknown future endpoint may be
    another. Falling back to the raw body is more useful than insisting.
    """
    payload = _safe_json(response)
    if isinstance(payload, dict) and "result" in payload:
        return payload["result"]
    return payload


def _safe_json(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None
