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


KIS_CONFIG_FILE = CONFIG_DIR / "kis_devlp.yaml"

# Trading settings live in whichever broker config is in use, so a Toss-only
# install never needs a KIS file. Defaults are here rather than duplicated in
# both YAML examples — two copies would drift, and the file that happens to be
# missing would silently change behaviour.
_TRADING_DEFAULTS: dict[str, Any] = {
    "default_unit_amount": 100_000,
    "default_unit_amount_usd": 100,
    "auto_trading": True,
    "default_mode": DEMO,
}


def _to_bool(value: Any, default: bool) -> bool:
    """YAML may hand back a real bool or the string a human typed."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int(value: Any, default: int, key: str) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        logger.warning("[BROKER] %s=%r is not a number; using %s", key, value, default)
        return default


def trading_settings() -> dict[str, Any]:
    """Trading settings from the configured broker's own file.

    Never raises and never requires a file. These values all have defaults, so a
    missing or unreadable config is a reason to fall back, not to refuse to
    start — the batch that dies at 09:00 has its own cost.

    Deliberately reads the YAML directly instead of importing `kis_auth`, which
    loads `kis_devlp.yaml` at module scope. Importing it here would make this
    module part of the very problem it exists to remove.
    """
    settings = dict(_TRADING_DEFAULTS)

    try:
        source = TOSS_CONFIG_FILE if selected_broker() == TOSS else KIS_CONFIG_FILE
    except BrokerConfigError:
        # An unsupported PRISM_BROKER is reported by selected_broker() at the
        # point it matters; here it just means "use the defaults".
        return settings

    raw: dict[str, Any] = {}
    if source.exists():
        try:
            with open(source, encoding="utf-8") as handle:
                raw = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError) as exc:
            logger.warning("[BROKER] could not read %s (%s); using defaults", source, exc)
            raw = {}

    for key in ("default_unit_amount", "default_unit_amount_usd"):
        if raw.get(key) is not None:
            settings[key] = _to_int(raw[key], _TRADING_DEFAULTS[key], key)
    if raw.get("auto_trading") is not None:
        settings["auto_trading"] = _to_bool(raw["auto_trading"], _TRADING_DEFAULTS["auto_trading"])
    if raw.get("default_mode") is not None:
        mode = str(raw["default_mode"]).strip().lower()
        if mode in {DEMO, REAL}:
            settings["default_mode"] = mode
        else:
            logger.warning("[BROKER] default_mode=%r in %s is not recognised; using demo",
                           raw["default_mode"], source)
    return settings


def buy_amount(market: str) -> int:
    """Per-order budget: environment, then the broker's file, then the default."""
    from_env = toss_buy_amount(market)
    if from_env is not None:
        return from_env
    key = "default_unit_amount" if market.upper() == "KR" else "default_unit_amount_usd"
    return int(trading_settings()[key])


def auto_trading_enabled() -> bool:
    """Whether orders may actually be placed, per the broker's config."""
    return bool(trading_settings()["auto_trading"])


def configured_mode() -> str:
    """`demo`/`real` from the broker's file. `PRISM_TRADING_MODE` still wins.

    `trading_mode()` is unchanged and remains the authority; this is only the
    fallback beneath it, so the two cannot disagree about which one applies.
    """
    if os.getenv("PRISM_TRADING_MODE"):
        return trading_mode()
    return str(trading_settings()["default_mode"])


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
