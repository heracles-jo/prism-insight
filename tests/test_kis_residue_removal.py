"""KIS residue removal (migration audit Phase 3).

These paths used "did a KIS module/config load?" as a stand-in for questions
that were actually about the configured broker. On a Toss install that proxy
answered wrongly in both directions: the KR dashboard labelled a real-money
account "demo", and the US dashboard rendered an empty portfolio.
"""

import asyncio
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_by_path(name: str, relative: str):
    """Load a module that is not importable as a package (tools/, examples/)."""
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── KR dashboard: mode comes from the configured broker ──────────────────────


def test_the_kr_dashboard_mode_comes_from_the_configured_broker(monkeypatch):
    dashboard = pytest.importorskip(
        "examples.generate_dashboard_json",
        reason="dashboard dependencies unavailable in this environment",
    )
    monkeypatch.setattr(
        "trading.brokers.settings.configured_mode", lambda: "real", raising=True
    )
    assert dashboard._configured_mode() == "real"


def test_the_kr_dashboard_still_renders_when_the_mode_is_unreadable(monkeypatch):
    """A dashboard that cannot name the mode must still run, not crash."""
    dashboard = pytest.importorskip("examples.generate_dashboard_json")

    def _boom():
        raise RuntimeError("no broker config")

    monkeypatch.setattr(
        "trading.brokers.settings.configured_mode", _boom, raising=True
    )
    assert dashboard._configured_mode() == "demo"


def test_the_kr_dashboard_no_longer_reads_the_kis_config():
    """Prose about the removal is fine; opening the file is not — the same
    distinction `test_migration_audit_scans` draws."""
    import re

    source = (REPO_ROOT / "examples/generate_dashboard_json.py").read_text()
    opens = [
        line for line in source.splitlines()
        if "kis_devlp" in line and re.search(r"open\(|join\(|/ ?[\"']kis_devlp", line)
    ]
    assert not opens, f"still builds or opens the KIS config: {opens}"
    assert "_cfg" not in source, "the parsed KIS config is still referenced"


# ── US dashboard: availability is a factory question ─────────────────────────


def test_the_us_dashboard_asks_the_factory_for_availability():
    source = (REPO_ROOT / "examples/generate_us_dashboard_json.py").read_text()
    # The gate is the factory import, not a KIS module load.
    assert "from trading.brokers.factory import us_trader" in source
    assert "KIS_US_AVAILABLE" not in source
    assert "prism_us_stock_trading" not in source


# ── stance_mark: the dead KIS imports are gone ───────────────────────────────


def test_stance_mark_has_no_vestigial_kis_imports():
    source = (REPO_ROOT / "stance_mark.py").read_text()
    assert "DomesticStockTrading" not in source
    assert "KisQuoteProvider" not in source


# ── readiness audit: KIS-only, and says so ───────────────────────────────────


def test_readiness_audit_skips_a_non_kis_broker(monkeypatch, capsys):
    monkeypatch.setenv("PRISM_BROKER", "toss")
    tool = _load_by_path("kr_pending_readiness", "tools/check_kr_pending_readiness.py")

    assert asyncio.run(tool.inquire_kis_open_sells()) == {}
    assert "KIS 전용" in capsys.readouterr().err


def test_readiness_audit_skips_an_unrecognised_broker(monkeypatch, capsys):
    """A PRISM_BROKER typo must skip, not fall through to the KIS path."""
    monkeypatch.setenv("PRISM_BROKER", "tos")
    tool = _load_by_path("kr_pending_readiness2", "tools/check_kr_pending_readiness.py")

    assert asyncio.run(tool.inquire_kis_open_sells()) == {}
    assert "스킵" in capsys.readouterr().err


# ── archive enrichment: KIS-only, said once ──────────────────────────────────


def test_archive_enrichment_says_it_is_kis_only_once(monkeypatch, caplog):
    monkeypatch.setenv("PRISM_BROKER", "toss")
    from cores.archive.data_enricher import KRDataEnricher

    enricher = KRDataEnricher()
    with caplog.at_level("WARNING"):
        assert enricher._get_trading() is None
        assert enricher._get_trading() is None

    notices = [r for r in caplog.records if "KIS 전용" in r.message]
    assert len(notices) == 1, "the notice must not repeat once per ticker"


# ── screening snapshot: no pointless KIS round-trip ──────────────────────────


@pytest.mark.parametrize(
    "broker, sources, expected",
    [
        ("kis", None, True),
        ("toss", None, False),
        ("toss", "kis,krx", True),   # opt-in still honoured
        ("toss", "krx,fdr", False),
    ],
)
def test_the_snapshot_skips_kis_when_it_is_not_configured(
    monkeypatch, broker, sources, expected
):
    monkeypatch.setenv("PRISM_BROKER", broker)
    if sources is None:
        monkeypatch.delenv("PRISM_MARKET_DATA_SOURCES", raising=False)
    else:
        monkeypatch.setenv("PRISM_MARKET_DATA_SOURCES", sources)

    # Compiled in isolation: importing trigger_batch pulls in the whole
    # screening stack, and this helper depends on nothing from it.
    source = (REPO_ROOT / "trigger_batch.py").read_text()
    start = source.index("def _kis_snapshot_usable")
    end = source.index("def load_market_snapshot_bundle")
    namespace: dict = {"os": __import__("os")}
    exec(source[start:end], namespace)  # noqa: S102 - our own source, one function

    assert namespace["_kis_snapshot_usable"]() is expected


# ── messaging subscribers: KIS-only, refuse to start otherwise ───────────────


@pytest.mark.parametrize(
    "relative",
    [
        "examples/messaging/redis_subscriber_example.py",
        "examples/messaging/gcp_pubsub_subscriber_example.py",
    ],
)
def test_the_subscribers_refuse_a_non_kis_broker(monkeypatch, relative):
    monkeypatch.setenv("PRISM_BROKER", "toss")
    module = _load_by_path(f"sub_{Path(relative).stem}", relative)

    assert module._refuse_non_kis_broker() is True
    assert "KIS only" in module.__doc__
