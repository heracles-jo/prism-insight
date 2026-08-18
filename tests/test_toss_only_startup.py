"""A Toss-only install must start without any KIS credentials on disk.

`trading/kis_auth.py` opens `trading/config/kis_devlp.yaml` at module scope, so
*importing* it is enough to crash a machine that never signed up for KIS. Three
entry points used to do exactly that at their own module scope, which made
`PRISM_BROKER=toss` unusable: the process died during import, before broker
selection was ever consulted.

These tests run each entry point in a subprocess with the KIS config hidden, so
a regression shows up as the same FileNotFoundError a Toss user would hit
rather than as a passing test on a developer machine that happens to have the
file. Subprocesses also keep the import graph honest — a module already in this
process's `sys.modules` would mask the very thing under test.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIS_CONFIG = REPO_ROOT / "trading" / "config" / "kis_devlp.yaml"

# Entry points a Toss-only operator actually starts. `stock_tracking_agent` is
# the trading loop itself, so it is the one that matters most.
TOSS_ENTRY_POINTS = [
    "stock_tracking_agent",
    "trading.portfolio_telegram_reporter",
    "examples.generate_dashboard_json",
    "prism_core.execution_service",
    "trading.brokers.factory",
    "cores.market_data",
]


@pytest.fixture
def without_kis_config():
    """Hide kis_devlp.yaml for the duration of a test, then put it back.

    Moved rather than deleted, and restored in a finally, so a failing test
    never costs the developer their real credentials.
    """
    if not KIS_CONFIG.exists():
        yield  # Already a Toss-only checkout; nothing to hide.
        return

    stash_dir = tempfile.mkdtemp(prefix="kis-config-")
    stashed = Path(stash_dir) / KIS_CONFIG.name
    shutil.move(str(KIS_CONFIG), str(stashed))
    try:
        yield
    finally:
        shutil.move(str(stashed), str(KIS_CONFIG))
        shutil.rmtree(stash_dir, ignore_errors=True)


def _import_in_subprocess(module: str, broker: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PRISM_BROKER": broker,
        # Keep the child off the developer's .env, which may point at either
        # broker and would otherwise decide the result.
        "PRISM_TRADING_MODE": "demo",
    }
    return subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.mark.parametrize("module", TOSS_ENTRY_POINTS)
def test_entry_point_imports_without_kis_config(module, without_kis_config):
    """With PRISM_BROKER=toss and no kis_devlp.yaml, the import must succeed."""
    result = _import_in_subprocess(module, broker="toss")

    assert result.returncode == 0, (
        f"{module} failed to import on a Toss-only install "
        f"(no kis_devlp.yaml):\n{result.stderr[-2000:]}"
    )


@pytest.mark.parametrize("module", TOSS_ENTRY_POINTS)
def test_entry_point_does_not_load_kis_auth_when_broker_is_toss(module):
    """Importing an entry point must not drag `trading.kis_auth` in.

    Import success alone is not enough: a machine that still has the YAML file
    would pass that check while quietly reading KIS credentials on every start.
    The module is what performs the read, so its absence from sys.modules is the
    property worth pinning.
    """
    probe = (
        f"import {module}\n"
        "import sys\n"
        "print('LOADED' if 'trading.kis_auth' in sys.modules else 'ABSENT')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT),
        env={**os.environ, "PRISM_BROKER": "toss", "PRISM_TRADING_MODE": "demo"},
        capture_output=True,
        text=True,
        timeout=180,
    )

    assert result.returncode == 0, f"{module} failed to import:\n{result.stderr[-2000:]}"
    assert "ABSENT" in result.stdout, (
        f"{module} imported trading.kis_auth even though PRISM_BROKER=toss; "
        "it reads kis_devlp.yaml at module scope, so this is a KIS credential "
        "read on every Toss startup."
    )


def test_kis_broker_still_reaches_kis_auth(without_kis_config):
    """The guard must not have turned into a silent no-op for KIS users.

    If a lazy import quietly swallowed the failure, KIS operators would get a
    dashboard with no positions instead of a clear error. Losing the config file
    has to still be visible somewhere.
    """
    result = _import_in_subprocess("trading.kis_auth", broker="kis")

    assert result.returncode != 0, (
        "trading.kis_auth imported with no kis_devlp.yaml present — the module "
        "is supposed to fail loudly so a misconfigured KIS install is obvious."
    )
    assert "kis_devlp.yaml" in result.stderr, (
        f"Expected the missing config file to be named in the error:\n{result.stderr[-2000:]}"
    )
