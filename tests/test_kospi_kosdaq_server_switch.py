"""The kospi_kosdaq tools must not need KRX credentials.

KRX made login mandatory, the PyPI `kospi-kosdaq-stock-server` has no
credential-free path, and its pykrx fallback is switched off. Every tool then
returns an error dict — quietly, because the batch still finishes and the
report is still produced, just without moving averages, RSI, MACD or investor
flows.

Three separate things decide which server answers, and they were not in
agreement:

  * `cores/llm/mcp_servers.yaml` — the report path, already migrated
  * `mcp_agent.config.yaml` — the analysis agents, via the mcp-agent framework
  * `cores/data_prefetch.py` — neither, it imports the module directly

The third is the one worth guarding hardest. Agents are built with
`server_names=[] if prefetched_data else ["kospi_kosdaq"]`, so prefetch is the
primary path and MCP is only the fallback: leaving prefetch on the PyPI package
would keep the main path broken while the config looked fixed.
"""

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# The real config is gitignored, so a checkout that has never been set up only
# has the example. Both are checked wherever they exist.
CONFIG_PATHS = ("mcp_agent.config.yaml", "mcp_agent.config.yaml.example",
                "cores/llm/mcp_servers.yaml")


def _server_config(rel: str):
    """The `kospi_kosdaq` block, across two different file shapes.

    `mcp_agent.config.yaml` nests servers under `mcp.servers`; the native
    registry puts them at the top level under `servers`.
    """
    path = REPO_ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} is not present in this checkout")

    config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    servers = config.get("mcp", {}).get("servers") or config.get("servers") or {}
    assert "kospi_kosdaq" in servers, f"{rel} has no kospi_kosdaq server"
    return servers["kospi_kosdaq"]


@pytest.mark.parametrize("rel", CONFIG_PATHS)
def test_every_registry_points_at_the_repo_server(rel):
    """One registry left behind is one path still asking KRX to log in."""
    assert _server_config(rel)["args"] == ["-m", "cores.market_data.mcp_server"]


@pytest.mark.parametrize("rel", CONFIG_PATHS)
def test_no_krx_credentials_are_required_anywhere(rel):
    """A credential named here is a credential the operator must go and obtain."""
    env = _server_config(rel).get("env") or {}

    leftovers = [key for key in env if key.startswith(("KRX_", "KAKAO_"))]

    assert not leftovers, f"{rel} still demands {leftovers}"


@pytest.mark.parametrize("rel", CONFIG_PATHS)
def test_the_child_can_import_the_repo(rel):
    """The server imports repo code, so PYTHONPATH is not optional here.

    Without it the child dies on `ModuleNotFoundError: cores`, which surfaces
    as the tool being unavailable rather than as a config error.
    """
    assert "PYTHONPATH" in (_server_config(rel).get("env") or {})


def test_prefetch_does_not_import_the_credentialed_package():
    """Prefetch bypasses MCP entirely, so the config swap does not reach it.

    Run in a subprocess because a module another test already imported would
    answer from `sys.modules` and hide what a fresh process actually loads.
    """
    probe = (
        "import cores.data_prefetch as dp\n"
        "print(dp._get_mcp_server_module().__name__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "cores.market_data.mcp_server" in result.stdout


def test_the_repo_server_starts_as_a_module():
    """`python -m` is how the config launches it; an import error is silent there.

    A missing `main` or a broken import shows up as a tool that never answers,
    with nothing in the report to say why.
    """
    probe = "import cores.market_data.mcp_server as m; print(callable(m.main))"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=300,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert "True" in result.stdout


def test_a_variable_with_a_default_is_not_reported_as_missing():
    """mcp_doctor's own rule: a false positive hides a real breakage.

    `${VAR:-default}` is the author saying what to do when the variable is
    absent. Reading only `${VAR}` made those look like literals, so an
    intentionally-optional entry was reported as an unset credential and
    kospi_kosdaq showed up unhealthy on a correctly configured host.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from tools.mcp_doctor import _check_env

    optional = _check_env(
        {"PRISM_REPORT_DATA_SOURCES": ""},
        {"PRISM_REPORT_DATA_SOURCES": "${PRISM_REPORT_DATA_SOURCES:-}"},
    )
    required = _check_env(
        {"FIRECRAWL_API_KEY": ""}, {"FIRECRAWL_API_KEY": "${FIRECRAWL_API_KEY}"}
    )

    assert optional[0]["set"] is True, "a defaulted variable is a choice, not a gap"
    # The other half of the rule: a variable with no default is still required,
    # so this must not have become a blanket suppression.
    assert required[0]["set"] is False
