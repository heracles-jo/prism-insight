"""Migration-audit scans (Phase 1): freeze known defects, catch new ones.

Companion to `test_no_module_scope_kis_import.py`, covering what neither of its
layers can see: code that reads the KIS config file without touching kis_auth,
KIS response shapes consumed outside the KIS adapters, calls to trader methods
no broker implements, and configuration keys that exist but are never read.

Every `KNOWN_*` set below is a frozen list of current offenders — do not add to
it. Each entry names the migration-audit phase
(.claude/PRPs/prds/full-migration-audit.prd.md) that removes it, and a stale
check fails when a fixed file is left in the set, which is what forces the
lists to shrink instead of quietly growing.

Note on grep patterns: `\\b` is not reliable in this platform's `git grep -E`,
so word boundaries are spelled as character classes.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Non-production trees, same shape as the sibling tripwire's ALLOWED_PREFIXES.
SKIP_PREFIXES = ("tests/", "prism-us/tests/", "trading/samples/")


def _git_grep(pattern: str) -> list[tuple[str, int, str]]:
    out = subprocess.run(
        ["git", "grep", "-nE", pattern, "--", "*.py"],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    ).stdout
    rows = []
    for line in out.splitlines():
        path, lineno, text = line.split(":", 2)
        if not path.startswith(SKIP_PREFIXES):
            rows.append((path, int(lineno), text))
    return rows


def _assert_frozen(found_paths: set, known: set, allowed: set, what: str):
    """The ratchet: no new offenders, and no stale allowlist entries."""
    offenders = found_paths - allowed
    stale = known - offenders
    assert not stale, (
        f"fixed but still allowlisted — remove from the KNOWN set for {what}: "
        + ", ".join(sorted(stale))
    )
    new = offenders - known
    assert not new, (
        f"new {what} offenders (fix them or, only with a written phase plan, "
        "extend the KNOWN set):\n  " + "\n  ".join(sorted(new))
    )


# ── kis_devlp.yaml read outside its owners ───────────────────────────────────

# Files whose job is the KIS config: the owner, the two KIS traders, and the
# broker settings module that names both brokers' files.
DEVLP_ALLOWED = {
    "trading/kis_auth.py",
    "trading/domestic_stock_trading.py",
    "prism-us/trading/us_stock_trading.py",
    "trading/brokers/settings.py",
}

KNOWN_DEVLP_OFFENDERS = {
    "examples/generate_dashboard_json.py",                  # Phase 3 — mode via trading_settings()
    "examples/messaging/gcp_pubsub_subscriber_example.py",  # Phase 3 — subscriber decision
}


def test_no_new_direct_reads_of_the_kis_config_file():
    """A file the census cannot flag: both offenders have except-fallbacks, so
    they import clean and still prefer the KIS config on a Toss install."""
    hits = {
        path
        for path, _lineno, text in _git_grep(r"kis_devlp\.yaml")
        # Only lines that build or open the path; prose mentions in comments,
        # docstrings and error messages are fine and common.
        if re.search(r"open\(|join\(|/ ?[\"']kis_devlp", text)
    }
    _assert_frozen(hits, KNOWN_DEVLP_OFFENDERS, DEVLP_ALLOWED,
                   "direct kis_devlp.yaml read")


# ── KIS response shapes outside the KIS adapters ─────────────────────────────

_SHAPE_PATTERN = (
    r"(^|[^A-Za-z0-9_])"
    r"(rt_cd|msg1|ORD_DVSN|output1|output2|sll_buy_dvsn_cd|psbl_qty|nccs_qty)"
    r"([^A-Za-z0-9_]|$)"
    r"|getBody\("
)

# Code that *is* the KIS integration; KIS field names are its vocabulary.
SHAPE_ALLOWED = {
    "trading/kis_auth.py",
    "trading/domestic_stock_trading.py",
    "prism-us/trading/us_stock_trading.py",
    "trading/brokers/kis_adapter.py",
    "cores/kis_market_snapshot.py",
    "cores/market_data/kis_source.py",
}

KNOWN_SHAPE_OFFENDERS = {
    "cores/archive/data_enricher.py",       # Phase 3 — KIS-direct enricher
    "prism_core/order_intents.py",          # Phase 6 — deliberate dual-shape (rt_cd|code)
    "prism_core/stance_adapter.py",         # Phase 6 — with order_intents
    "tools/check_kr_pending_readiness.py",  # Phase 3 — reads KIS rows unguarded
    "tools/fill_chaser.py",                 # Phase 5 — with the BrokerPort gap below
}


def test_kis_response_shapes_stay_inside_the_kis_adapters():
    """Field names like rt_cd travelling outside the adapters is how a Toss
    response silently reads as 'no data' instead of failing loudly."""
    hits = {path for path, _lineno, _text in _git_grep(_SHAPE_PATTERN)}
    _assert_frozen(hits, KNOWN_SHAPE_OFFENDERS, SHAPE_ALLOWED,
                   "KIS response shape")


# ── Trader methods called that no broker port defines ────────────────────────

# Methods production code actually calls on trader objects obtained through the
# factory / ExecutionService, per the 2026-08-18 audit. Curated, not grepped:
# a grep for `.method(` over every object would drown in false positives.
CALLED_ON_TRADERS = {
    "tools/fill_chaser.py": {"get_revisable_orders", "get_unfilled_orders"},
    "prism-us/us_stock_tracking_agent.py": {"is_market_open",
                                            "is_reserved_order_available"},
}

# (file, method) -> where the implementation is missing. "toss" means
# TossBroker lacks it (the call silently no-ops or AttributeErrors under
# PRISM_BROKER=toss); "+port" means BrokerPort does not declare it either.
KNOWN_CONTRACT_GAPS = {
    ("tools/fill_chaser.py", "get_revisable_orders"): "toss",        # Phase 5
    ("tools/fill_chaser.py", "get_unfilled_orders"): "toss",         # Phase 5
}


def test_broker_port_method_calls_match_the_implementations():
    """us_stock_tracking_agent.py:2802 calls is_market_open on whatever the
    factory returned; under Toss that AttributeErrors into the full_exit
    branch. This test pins the gap until the port and adapter close it."""
    from trading.brokers.base import BrokerPort
    from trading.brokers.toss.adapter import TossBroker

    # The curated call sites must still exist, or the table is stale. Matched
    # without the trailing paren: fill_chaser passes bound methods into
    # asyncio.to_thread(trader.get_revisable_orders) rather than calling them.
    for path, methods in CALLED_ON_TRADERS.items():
        text = (REPO_ROOT / path).read_text(encoding="utf-8")
        for method in methods:
            assert f".{method}" in text, (
                f"{path} no longer uses {method} — update CALLED_ON_TRADERS"
            )

    # Gap bookkeeping must match reality, in both directions.
    for (path, method), where in KNOWN_CONTRACT_GAPS.items():
        assert not hasattr(TossBroker, method), (
            f"TossBroker now implements {method} — remove "
            f"({path!r}, {method!r}) from KNOWN_CONTRACT_GAPS"
        )
        if "port" in where:
            assert not hasattr(BrokerPort, method), (
                f"BrokerPort now declares {method} — update the "
                f"({path!r}, {method!r}) entry"
            )

    for path, methods in CALLED_ON_TRADERS.items():
        for method in methods:
            if hasattr(TossBroker, method):
                continue
            assert (path, method) in KNOWN_CONTRACT_GAPS, (
                f"{path} calls {method}, which TossBroker does not implement "
                "and which is not recorded in KNOWN_CONTRACT_GAPS"
            )


# ── Configuration keys that exist but are never read ─────────────────────────


def test_the_configured_buy_amount_reaches_the_order_path():
    """Editing default_unit_amount in toss_config.yaml must change order size.

    Static proxy for that behaviour: the env → broker file → default chain in
    `settings.buy_amount()` must have at least one caller on the order path.
    """
    callers = [
        (path, lineno)
        for path, lineno, text in _git_grep(r"[^A-Za-z0-9_]buy_amount\(")
        if path != "trading/brokers/settings.py"
        and not text.lstrip().startswith("#")
        and "def buy_amount" not in text
    ]
    assert callers, "settings.buy_amount() is dead code: no production caller"


def test_trading_settings_serves_the_broker_trading_keys():
    """Baseline that must hold today: trading_settings() answers the three keys
    entry points need, from the broker file or from defaults."""
    from trading.brokers.settings import trading_settings

    settings = trading_settings()
    for key in ("default_unit_amount", "auto_trading", "default_mode"):
        assert key in settings, f"trading_settings() lost the {key!r} key"
