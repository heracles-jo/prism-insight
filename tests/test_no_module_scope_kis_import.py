"""A rule, not a sample: production code must not reach KIS at import time.

`trading/kis_auth.py` opens `trading/config/kis_devlp.yaml` while it is being
imported. Any module that imports it at module scope therefore demands KIS
credentials from every install that loads that module — including one that set
`PRISM_BROKER=toss` and never signed up for KIS.

Phase 2 fixed the three entry points that were known to crash. A census then
found two more (`weekly_insight_report`, `examples/generate_us_dashboard_json`)
that the sample had missed, which is the argument for this file: enumerating
entry points by hand misses them, so the property gets checked instead.

Two layers, because one alone is not enough:

  * The AST scan catches a *direct* module-scope import and names the file and
    line, so a violation is obvious at the point it is introduced.
  * The import census catches the *transitive* case, which is how the worst one
    actually happened — `portfolio_telegram_reporter` imported the US trading
    module, which imported `kis_auth`. No amount of grepping that file for
    "kis_auth" would have found it.

Neither layer subsumes the other. A file can be clean under the AST scan and
still drag KIS in through a dependency; a module not on the census list can
still be a lazily-imported landmine for whoever imports it next.
"""

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIS_CONFIG = REPO_ROOT / "trading" / "config" / "kis_devlp.yaml"

# Importing any of these executes trading/kis_auth.py, directly or as their own
# first act, so a module-scope import of them costs the same.
KIS_MODULES = {
    "kis_auth",
    "trading.kis_auth",
    "domestic_stock_trading",
    "trading.domestic_stock_trading",
    "us_stock_trading",
    "trading.us_stock_trading",
}

# Code that is legitimately KIS-specific: it exists only to talk to KIS, so
# importing kis_auth at module scope is what it is for. A Toss-only install
# never loads these, because the factory never selects them.
ALLOWED = {
    "trading/domestic_stock_trading.py",     # the KIS domestic trader itself
    "trading/kis_auth.py",                   # the module in question
    "prism-us/trading/us_stock_trading.py",  # the KIS overseas trader itself
    "cores/market_data/kis_source.py",       # *is* the KIS market data source
    "cores/kis_market_snapshot.py",          # KIS-specific by name
    "prism-us/us_pending_order_batch.py",    # drains a queue only KIS creates
}

# Reference scripts, not part of any runtime path. They are KIS API samples.
ALLOWED_PREFIXES = ("trading/samples/", "tests/", "prism-us/tests/")

# Entry points an operator actually starts. Each is imported twice — once with a
# leftover kis_devlp.yaml present and once with none at all — and anything
# reachable from them is covered transitively.
ENTRY_POINTS = [
    "stock_tracking_agent",
    "trigger_batch",
    "stance_server",
    "stance_mark",
    "weekly_insight_report",
    "stock_analysis_orchestrator",
    "telegram_bot_agent",
    "trading.portfolio_telegram_reporter",
    "examples.generate_dashboard_json",
    "examples.generate_us_dashboard_json",
    "prism_core.execution_service",
    "trading.brokers.factory",
    "cores.market_data",
]


def _module_scope_imports(tree: ast.Module):
    """Yield (lineno, module_name) for imports that run at import time.

    `try`/`if`/`with` at the top level still execute on import, so their bodies
    count; a `def` or `class` body does not, which is exactly the distinction
    grep cannot make and the whole reason this is an AST walk.
    """
    pending = list(tree.body)
    while pending:
        node = pending.pop()

        if isinstance(node, (ast.Try, ast.If, ast.With)):
            for attr in ("body", "orelse", "finalbody", "handlers"):
                pending.extend(getattr(node, attr, None) or [])
        elif isinstance(node, ast.ExceptHandler):
            pending.extend(node.body)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield node.lineno, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module:
                yield node.lineno, module
            for alias in node.names:
                yield node.lineno, f"{module}.{alias.name}" if module else alias.name


def _tracked_python_files():
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
    ).stdout.split()
    return [p for p in out if not p.startswith(ALLOWED_PREFIXES) and p not in ALLOWED]


def test_no_production_module_imports_kis_at_module_scope():
    """The rule that replaces hand-enumerating entry points."""
    offenders = []
    for rel in _tracked_python_files():
        path = REPO_ROOT / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue  # Not our concern; other tooling owns syntax.

        for lineno, name in _module_scope_imports(tree):
            if name in KIS_MODULES:
                offenders.append(f"{rel}:{lineno} imports {name}")

    assert not offenders, (
        "these import KIS at module scope, so loading them requires "
        "kis_devlp.yaml even when PRISM_BROKER=toss:\n  "
        + "\n  ".join(sorted(offenders))
        + "\n\nMove the import inside the function that needs it (see "
          "weekly_insight_report._get_primary_account_key), or add the file to "
          "ALLOWED if it is genuinely KIS-only."
    )


@contextmanager
def _kis_config_hidden():
    """Move kis_devlp.yaml aside for the duration of the block, then put it back.

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


def _probe_import(module: str) -> subprocess.CompletedProcess:
    """Import `module` under PRISM_BROKER=toss in a clean interpreter.

    A subprocess, not an import here, because a module already in this process's
    `sys.modules` would mask the very thing under test.
    """
    probe = (
        f"import {module}\n"
        "import sys\n"
        "print('KIS_LOADED' if 'trading.kis_auth' in sys.modules else 'CLEAN')\n"
    )
    return subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT),
        env={**os.environ, "PRISM_BROKER": "toss", "PRISM_TRADING_MODE": "demo"},
        capture_output=True, text=True, timeout=300,
    )


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_entry_point_ignores_a_leftover_kis_config(module):
    """Switching to Toss must stop the KIS reads, even with the old file present.

    This is the state most installs are actually in: someone set
    `PRISM_BROKER=toss` on a machine that used to run KIS, and `kis_devlp.yaml`
    is still sitting there. Hiding the file would let a module that reads it
    unconditionally pass, so this case is checked with the file in place.
    """
    result = _probe_import(module)

    assert result.returncode == 0, (
        f"{module} failed to import with PRISM_BROKER=toss:\n{result.stderr[-2000:]}"
    )
    assert "CLEAN" in result.stdout, (
        f"{module} loaded trading.kis_auth under PRISM_BROKER=toss. That module "
        "reads kis_devlp.yaml at import time, so this is a KIS credential read "
        "on every Toss startup."
    )


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_entry_point_starts_with_no_kis_config_at_all(module):
    """And an install that never had KIS must start too — the PRD's signal.

    Distinct from the test above, not implied by it: a module can leave
    `kis_auth` alone and still open `kis_devlp.yaml` itself, which is exactly
    what both dashboard generators used to do.
    """
    with _kis_config_hidden():
        result = _probe_import(module)

    assert result.returncode == 0, (
        f"{module} failed to import on a Toss-only install (no kis_devlp.yaml):"
        f"\n{result.stderr[-2000:]}"
    )
    assert "CLEAN" in result.stdout, (
        f"{module} loaded trading.kis_auth on an install with no KIS config."
    )


def test_kis_broker_still_fails_loudly_without_its_config():
    """The other half of the rule: KIS users must still get a clear error.

    Every fix above moves a KIS import somewhere it can fail quietly. If one of
    them swallowed the failure instead, a misconfigured KIS install would show
    an empty dashboard rather than say what is wrong, which is worse than the
    crash this work removed. Losing the config file has to stay visible.
    """
    with _kis_config_hidden():
        result = subprocess.run(
            [sys.executable, "-c", "import trading.kis_auth"],
            cwd=str(REPO_ROOT),
            env={**os.environ, "PRISM_BROKER": "kis", "PRISM_TRADING_MODE": "demo"},
            capture_output=True, text=True, timeout=300,
        )

    assert result.returncode != 0, (
        "trading.kis_auth imported with no kis_devlp.yaml present — it is "
        "supposed to fail loudly so a misconfigured KIS install is obvious."
    )
    assert "kis_devlp.yaml" in result.stderr, (
        "Expected the missing config file to be named in the error:\n"
        + result.stderr[-2000:]
    )
