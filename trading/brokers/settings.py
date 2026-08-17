"""Which broker to use, and the credentials it needs.

Selection is installation-wide and lives in one environment variable, because
mixing brokers per account would mean reconciling two ledgers, two fee models
and two settlement calendars inside one portfolio view — a much larger change
than this work is.

Two defaults here are safety defaults rather than convenience ones.

`PRISM_BROKER` defaults to `kis`, so an installation that never heard of this
feature keeps behaving exactly as before. Silence means "no change".

The trading mode defaults to `demo`, and anything unrecognised also resolves to
`demo`. Toss has no paper-trading server, so `real` there means real money on
the first order. A typo in an environment variable must not be the thing that
decides that.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
TOSS_CONFIG_FILE = CONFIG_DIR / "toss_config.yaml"

KIS = "kis"
TOSS = "toss"
SUPPORTED_BROKERS = frozenset({KIS, TOSS})

DEMO = "demo"
REAL = "real"


class BrokerConfigError(RuntimeError):
    """The broker was selected but cannot be configured."""


def selected_broker() -> str:
    """The configured broker, lowercase. `kis` unless told otherwise."""
    raw = (os.getenv("PRISM_BROKER") or KIS).strip().lower()
    if raw not in SUPPORTED_BROKERS:
        raise BrokerConfigError(
            f"PRISM_BROKER={raw!r} is not supported; "
            f"choose one of {sorted(SUPPORTED_BROKERS)}"
        )
    return raw


def trading_mode() -> str:
    """`demo` or `real`, defaulting to `demo`.

    An unrecognised value resolves to `demo` and logs loudly rather than
    raising: refusing to start is not obviously safer than starting in the
    harmless mode, and a batch that dies at 09:00 has its own cost.
    """
    raw = (os.getenv("PRISM_TRADING_MODE") or DEMO).strip().lower()
    if raw not in {DEMO, REAL}:
        logger.error(
            "[BROKER] PRISM_TRADING_MODE=%r is not recognised; falling back to demo", raw
        )
        return DEMO
    return raw


def is_demo() -> bool:
    return trading_mode() == DEMO


def load_toss_config(path: Path | None = None) -> dict[str, Any]:
    """Read `toss_config.yaml`, with env overrides for containerised runs."""
    target = path or TOSS_CONFIG_FILE
    config: dict[str, Any] = {}

    if target.exists():
        try:
            with open(target, encoding="utf-8") as handle:
                config = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise BrokerConfigError(f"could not read {target}: {exc}") from exc

    # Environment wins, so secrets can be injected without a file on disk.
    for key, env in (
        ("client_id", "TOSS_CLIENT_ID"),
        ("client_secret", "TOSS_CLIENT_SECRET"),
        ("account_seq", "TOSS_ACCOUNT_SEQ"),
        ("base_url", "TOSS_BASE_URL"),
    ):
        value = os.getenv(env)
        if value:
            config[key] = value

    missing = [k for k in ("client_id", "client_secret") if not config.get(k)]
    if missing:
        raise BrokerConfigError(
            f"Toss is selected but {', '.join(missing)} is missing.\n"
            f"  copy {target.name}.example to {target.name} and fill it in, "
            f"or set TOSS_CLIENT_ID / TOSS_CLIENT_SECRET."
        )
    return config


def toss_buy_amount(market: str) -> int | None:
    """Per-order budget, if configured. `None` lets the adapter default."""
    env = "PRISM_BUY_AMOUNT_KRW" if market.upper() == "KR" else "PRISM_BUY_AMOUNT_USD"
    raw = os.getenv(env)
    if not raw:
        return None
    try:
        return int(float(raw))
    except ValueError:
        logger.warning("[BROKER] %s=%r is not a number; ignoring", env, raw)
        return None
