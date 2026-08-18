"""Making an import lazy is not enough — the call site has to still work.

Moving `from trading import kis_auth as ka` out of module scope leaves every
`ka.` reference in that file undefined. Nothing catches it at import time, so
`tests/test_no_module_scope_kis_import.py` passes, the module loads cleanly, and
the failure waits until the function is actually called.

That is exactly what happened: `portfolio_telegram_reporter` imported fine and
raised `NameError: name 'ka' is not defined` the first time a real run asked for
the portfolio. It was found by starting the system, not by any test.

So these tests call the functions whose imports were made lazy, under
`PRISM_BROKER=toss`, and assert they return something usable rather than raise.
Network is never touched: each test stubs the boundary its function crosses.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import trading.brokers.settings as settings


@pytest.fixture
def toss_selected(monkeypatch, tmp_path):
    """PRISM_BROKER=toss with a config that exists and no KIS file in sight."""
    monkeypatch.setenv("PRISM_BROKER", "toss")
    monkeypatch.setenv("PRISM_TRADING_MODE", "real")
    monkeypatch.setattr(
        settings,
        "load_toss_config",
        lambda *a, **k: {
            "client_id": "c_test",
            "client_secret": "s_test",
            "account_seq": "1234567",
        },
    )
    monkeypatch.setattr(settings, "KIS_CONFIG_FILE", tmp_path / "absent-kis.yaml")


def test_the_reporter_resolves_an_account_without_kis(toss_selected):
    """The exact NameError a live run hit: `ka` was gone but still referenced."""
    from trading.portfolio_telegram_reporter import PortfolioTelegramReporter

    reporter = PortfolioTelegramReporter(
        telegram_token="t", chat_id="c", trading_mode="real"
    )

    account = reporter._get_primary_account_config("kr")

    assert account is not None, "a Toss install must resolve its own account"
    # These two keys are the whole contract — the callers pass them straight to
    # the factory, which ignores them on the Toss path.
    assert account["name"] and account["product"]


def test_the_reporter_resolves_the_us_account_too(toss_selected):
    from trading.portfolio_telegram_reporter import PortfolioTelegramReporter

    reporter = PortfolioTelegramReporter(
        telegram_token="t", chat_id="c", trading_mode="real"
    )

    assert reporter._get_primary_account_config("us") is not None


def test_a_broken_account_lookup_drops_the_section_rather_than_crashing(
    monkeypatch, toss_selected
):
    """A missing account must cost that section of the report, not the report."""
    from trading.portfolio_telegram_reporter import PortfolioTelegramReporter

    monkeypatch.setattr(
        settings, "load_toss_config", lambda *a, **k: {"client_id": "c", "client_secret": "s"}
    )
    reporter = PortfolioTelegramReporter(
        telegram_token="t", chat_id="c", trading_mode="real"
    )

    assert reporter._get_primary_account_config("kr") is None


def test_us_trading_availability_asks_the_factory(toss_selected):
    """It used to ask "can I import a KIS class", and answered no on Toss.

    That silently dropped every US position from the report while `us_trader()`
    would have served them.
    """
    from trading.portfolio_telegram_reporter import _us_trading_available

    assert _us_trading_available() is True


def test_dashboard_live_data_availability_asks_the_factory(toss_selected):
    """Same defect in the KR dashboard: an empty portfolio instead of holdings."""
    from examples.generate_dashboard_json import _live_trading_available

    assert _live_trading_available() is True


def test_the_weekly_report_scopes_by_the_key_the_rows_were_stamped_with(toss_selected):
    """Query key and write key must come from the same resolver.

    Migration stamps account_key from `primary_account_scope()`. Asking
    `kis_auth` here instead returned a KIS account on a machine whose rows are
    filed under its Toss one, so every weekly number came back empty — no error,
    just nothing. Pinned against the writer rather than a literal, so the two
    cannot drift apart.
    """
    import weekly_insight_report

    expected = settings.primary_account_scope("kr")[0]

    assert weekly_insight_report._get_primary_account_key("kr") == expected
    assert expected.startswith("prod:1234567")  # the Toss account, not a KIS one


def test_the_us_dashboard_scopes_by_the_same_key(toss_selected):
    """Same desync, same consequence: an empty dashboard instead of holdings."""
    from examples.generate_us_dashboard_json import USDashboardDataGenerator

    generator = USDashboardDataGenerator.__new__(USDashboardDataGenerator)

    assert generator._get_primary_account_key() == settings.primary_account_scope("us")[0]


def test_an_unresolvable_account_leaves_the_report_unscoped(monkeypatch, toss_selected):
    """No account is a reason to stop scoping, not a reason to stop reporting."""
    import weekly_insight_report

    monkeypatch.setattr(
        settings, "load_toss_config", lambda *a, **k: {"client_id": "c", "client_secret": "s"}
    )

    assert weekly_insight_report._get_primary_account_key("kr") is None


def test_the_trading_loop_books_positions_under_the_key_it_reads(toss_selected):
    """The loop's account must match the one rows are stamped with.

    This was the worst of the desyncs, because it was not merely an empty
    query. `_get_trading_accounts` asked kis_auth unconditionally, so on a Toss
    install the loop ran under `vps:<kis_account>:01` — wrong broker *and*
    wrong mode — while migration wrote `prod:<toss_seq>:01`. Buys placed on the
    live Toss account were then booked under a KIS demo key that the holdings
    scan never selects, so those positions had no stop-loss, no target and no
    exit path at all.
    """
    import stock_tracking_agent

    agent = stock_tracking_agent.StockTrackingAgent.__new__(
        stock_tracking_agent.StockTrackingAgent
    )
    accounts = agent._get_trading_accounts()

    assert len(accounts) == 1, "Toss has a single account; fan-out is a KIS feature"
    assert accounts[0]["account_key"] == settings.primary_account_scope("kr")[0]


def test_account_log_labels_do_not_need_kis_config(toss_selected):
    """Masking is pure string work, but it used to live behind a config read.

    `kis_auth.mask_account_number` sits in a module that opens kis_devlp.yaml on
    import, so on a Toss-only install every log line naming an account raised.
    """
    import stock_tracking_agent

    label = stock_tracking_agent.StockTrackingAgent._safe_account_log_label(
        {"name": "toss-primary", "account_key": "prod:12345678:01"}
    )

    assert "12345678" not in label, "the raw account number reached the log"
    assert "toss-primary" in label and "prod:" in label


@pytest.mark.parametrize(
    "number", ["", "1", "1234", "12345", "123456", "12345678", "1234567890"]
)
def test_the_local_mask_matches_the_kis_one(number):
    """The copy must stay identical to the original, or it is a silent divergence."""
    from trading.kis_auth import mask_account_number as kis_mask

    from stock_tracking_agent import _mask_account_number as local_mask

    assert local_mask(number) == kis_mask(number)
