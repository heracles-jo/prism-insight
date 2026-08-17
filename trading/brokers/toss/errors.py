"""Toss API errors, and which of them a caller can do anything about.

Toss returns two different error shapes. `/oauth2/token` follows the OAuth2
standard (`error`, `error_description`); everything else uses a BFF envelope
(`error.requestId`, `error.code`, `error.message`, optional `error.data`). Both
are normalised into `TossApiError` here so nothing downstream has to know which
endpoint it called.

The split that matters is between transport-level failure and business refusal.
A 403 from an unregistered IP and a 422 refusing an out-of-hours order are both
"non-200", but the first means the installation is misconfigured and the second
means the market is closed — and only one of them is worth retrying. Mapping is
done at this boundary so the adapter above can stay readable.
"""

from __future__ import annotations

from typing import Any


class TossApiError(Exception):
    """A non-2xx response from Toss, in normalised form.

    Carries `code` rather than only a message because Toss says explicitly that
    `message` may come back empty when disclosure is restricted, and that
    clients should key their own text off `code`.
    """

    def __init__(
        self,
        code: str,
        message: str = "",
        *,
        status: int | None = None,
        request_id: str | None = None,
        data: dict[str, Any] | None = None,
    ):
        self.code = code
        self.message = message
        self.status = status
        self.request_id = request_id
        self.data = data or {}
        super().__init__(self._describe())

    def _describe(self) -> str:
        parts = [f"{self.code}"]
        if self.message:
            parts.append(self.message)
        if self.status is not None:
            parts.append(f"HTTP {self.status}")
        if self.request_id:
            # Toss asks for this when raising a support ticket, so it must
            # survive into the log rather than being dropped as noise.
            parts.append(f"requestId={self.request_id}")
        return " | ".join(parts)

    @property
    def is_business_refusal(self) -> bool:
        """True when Toss understood the request and declined it on the merits.

        These must not be retried and must not be reported as infrastructure
        faults: an order refused because the market is closed is an answer.
        """
        return self.status in {400, 409, 422}


class TossRateLimited(TossApiError):
    """429. Carries the server's own advice on how long to wait."""

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any):
        self.retry_after = retry_after
        super().__init__(*args, **kwargs)


class TossAuthError(TossApiError):
    """401/403 — the credentials or the caller's IP were rejected."""


def parse_error_response(status: int, payload: Any, headers: Any = None) -> TossApiError:
    """Turn a non-2xx response into the right exception type.

    Tolerates a body that is missing, empty, or not JSON at all: a proxy or a
    maintenance page will not follow either documented schema, and a parser that
    only handles the happy path turns an outage into a TypeError.
    """
    headers = headers or {}
    code, message, request_id, data = _extract(payload)

    if request_id is None:
        request_id = headers.get("X-Request-Id") or headers.get("x-request-id")

    if status == 429:
        return TossRateLimited(
            code or "rate-limit-exceeded",
            message,
            status=status,
            request_id=request_id,
            data=data,
            retry_after=_retry_after_seconds(headers),
        )
    if status in {401, 403}:
        return TossAuthError(
            code or ("unauthorized" if status == 401 else "access-denied"),
            message,
            status=status,
            request_id=request_id,
            data=data,
        )
    return TossApiError(
        code or f"http-{status}",
        message,
        status=status,
        request_id=request_id,
        data=data,
    )


def _extract(payload: Any) -> tuple[str, str, str | None, dict[str, Any]]:
    """Read whichever of the two documented error shapes this is."""
    if not isinstance(payload, dict):
        return "", "", None, {}

    # BFF envelope: {"error": {"requestId", "code", "message", "data"}}
    envelope = payload.get("error")
    if isinstance(envelope, dict):
        return (
            str(envelope.get("code") or ""),
            str(envelope.get("message") or ""),
            envelope.get("requestId"),
            envelope.get("data") if isinstance(envelope.get("data"), dict) else {},
        )

    # OAuth2 standard: {"error": "invalid_client", "error_description": "..."}
    if isinstance(envelope, str):
        return envelope, str(payload.get("error_description") or ""), None, {}

    return "", "", None, {}


def _retry_after_seconds(headers: Any) -> float | None:
    """Prefer `Retry-After`, fall back to `X-RateLimit-Reset`.

    Both are documented as integer seconds. A malformed value is treated as
    absent rather than raising — being rate limited is already the bad path and
    should not also be a crash.
    """
    for header in ("Retry-After", "X-RateLimit-Reset"):
        raw = headers.get(header) or headers.get(header.lower())
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            return value
    return None
