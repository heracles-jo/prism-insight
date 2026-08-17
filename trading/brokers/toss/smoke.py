"""Connectivity check against the real Toss API.

    python -m trading.brokers.toss.smoke

Verifies the three things that break independently — credentials, IP allowlist,
and account header — and says which one failed. Kept out of the test suite
because it needs real credentials and makes real calls; it is the manual step
that closes PRD Phase 2's success signal.

Read-only. It never touches an order endpoint.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from trading.brokers.base import BrokerUnavailable
from trading.brokers.toss.auth import CONFIG_DIR, TossAuth, TossCredentials
from trading.brokers.toss.client import TossClient
from trading.brokers.toss.errors import TossApiError

logger = logging.getLogger(__name__)

CONFIG_FILE = CONFIG_DIR / "toss_config.yaml"


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or CONFIG_FILE
    if not target.exists():
        raise SystemExit(
            f"missing {target}\n"
            f"copy {target.with_suffix('.yaml.example').name} and fill it in"
        )
    with open(target, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    config = load_config()

    missing = [k for k in ("client_id", "client_secret") if not config.get(k)]
    if missing:
        print(f"FAIL  config is missing: {', '.join(missing)}")
        return 1

    credentials = TossCredentials(
        config["client_id"],
        config["client_secret"],
        base_url=config.get("base_url") or "https://openapi.tossinvest.com",
    )
    auth = TossAuth(credentials)
    client = TossClient(auth, account_seq=config.get("account_seq"))

    # 1. Credentials + IP allowlist, in one call.
    try:
        auth.access_token()
        print("OK    token issued")
    except (TossApiError, BrokerUnavailable) as exc:
        print(f"FAIL  token: {exc}")
        return 1

    # 2. An authenticated read that needs no account header.
    try:
        accounts = client.get("/api/v1/accounts")
        print(f"OK    GET /api/v1/accounts → {_summarise(accounts)}")
    except (TossApiError, BrokerUnavailable) as exc:
        print(f"FAIL  accounts: {exc}")
        return 1

    # 3. The account header, which fails separately from everything above.
    if not config.get("account_seq"):
        print("SKIP  holdings — account_seq not set in toss_config.yaml")
        return 0

    try:
        holdings = client.get("/api/v1/holdings", needs_account=True)
        print(f"OK    GET /api/v1/holdings → {_summarise(holdings)}")
    except (TossApiError, BrokerUnavailable) as exc:
        print(f"FAIL  holdings: {exc}")
        return 1

    print("\nAll checks passed.")
    return 0


def _summarise(payload: Any) -> str:
    if isinstance(payload, list):
        return f"{len(payload)} item(s)"
    if isinstance(payload, dict):
        return f"keys: {sorted(payload)[:6]}"
    return type(payload).__name__


if __name__ == "__main__":
    sys.exit(main())
