"""Toss Securities Open API — auth, transport, and rate limiting.

Importing this package does not read configuration or contact anything. Build a
client explicitly:

    from trading.brokers.toss import TossAuth, TossClient, TossCredentials

    auth = TossAuth(TossCredentials(client_id, client_secret))
    client = TossClient(auth, account_seq=account_seq)
    accounts = client.get("/api/v1/accounts")

The trading adapter that turns this into orders arrives in PRD Phase 4.
"""

from trading.brokers.toss.auth import DEFAULT_BASE_URL, TossAuth, TossCredentials
from trading.brokers.toss.client import TossClient
from trading.brokers.toss.errors import (
    TossApiError,
    TossAuthError,
    TossRateLimited,
    parse_error_response,
)

__all__ = [
    "DEFAULT_BASE_URL",
    "TossApiError",
    "TossAuth",
    "TossAuthError",
    "TossClient",
    "TossCredentials",
    "TossRateLimited",
    "parse_error_response",
]
