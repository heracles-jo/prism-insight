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


# Product code for a Toss account. KIS distinguishes products (01 = 종합위탁,
# and others) within one account number; Toss does not, so the slot is filled
# with the KIS default to keep the three-part account_key shape that consumers
# such as `stock_tracking_agent._safe_account_log_label` split on.
_TOSS_PRODUCT_CODE = "01"


def primary_account_scope(
    market: str, *, kis_auth_loader: Any | None = None
) -> tuple[str, str, str, str]:
    """Identity of the account this install trades under, per the chosen broker.

    Returns `(account_key, account_name, product_code, mode)`. `account_key`
    keeps KIS's `{svr}:{account}:{product}` shape for every broker, because the
    value is stored in the DB and split by existing callers.

    This exists because DB migration has to stamp an owner onto legacy rows, and
    it used to ask `kis_auth` directly. That made a Toss user create
    `kis_devlp.yaml` — a file they have no account in — just to name the rows.
    The question is "who does this install trade as", which is the broker's to
    answer, not KIS's.

    `kis_auth_loader` is for callers that cannot reach the root `trading`
    package by name: `prism-us/trading/` shadows it on `sys.path`, so the US
    migration loads `kis_auth` from an explicit path instead. It is a callable
    rather than a module so that a Toss install never loads it at all — the
    whole point being that importing it reads `kis_devlp.yaml`.
    """
    broker = selected_broker()

    if broker == TOSS:
        config = load_toss_config()
        account_seq = str(config.get("account_seq") or "").strip()
        if not account_seq:
            raise BrokerConfigError(
                "Toss is selected but account_seq is missing.\n"
                f"  set account_seq in {TOSS_CONFIG_FILE.name}, or set "
                "TOSS_ACCOUNT_SEQ."
            )
        mode = trading_mode()
        svr = "vps" if mode == DEMO else "prod"
        name = str(config.get("account_name") or "toss-primary").strip()
        return (
            f"{svr}:{account_seq}:{_TOSS_PRODUCT_CODE}",
            name,
            _TOSS_PRODUCT_CODE,
            mode,
        )

    # Loaded here, not at module scope: kis_auth reads kis_devlp.yaml on import,
    # and this module exists to keep that off a Toss install's path.
    if kis_auth_loader is not None:
        ka = kis_auth_loader()
    else:
        from trading import kis_auth as ka

    default_mode = str(ka.getEnv().get("default_mode", DEMO)).strip().lower()
    svr = "vps" if default_mode == DEMO else "prod"
    account = ka.resolve_account(svr=svr, market=market)
    mode = DEMO if account["svr"] == "vps" else REAL
    return account["account_key"], account["name"], account["product"], mode


def broker_config_hint() -> str:
    """Where to fix account configuration, named for the broker actually chosen.

    Error messages used to point everyone at `kis_devlp.yaml`, which sends a
    Toss user to a file that is not theirs and has nothing to do with the
    failure they are looking at.
    """
    try:
        broker = selected_broker()
    except BrokerConfigError:
        return (
            "PRISM_BROKER is set to an unsupported value; "
            f"expected one of {', '.join(sorted(SUPPORTED_BROKERS))}."
        )

    if broker == TOSS:
        return (
            f"Please ensure account_seq is set in {TOSS_CONFIG_FILE.name} "
            "(or TOSS_ACCOUNT_SEQ)."
        )
    return f"Please ensure at least one account is configured in {KIS_CONFIG_FILE.name}."
