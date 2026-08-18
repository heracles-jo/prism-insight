"""Who does this install trade as? The broker answers, not KIS.

DB migration stamps an owner onto legacy rows, and it used to get that owner
from `kis_auth.resolve_account`. On a Toss install that raised, and the error
told the operator to configure an account in `kis_devlp.yaml` — a file holding
credentials for a broker they had deliberately switched away from. Creating one
just to satisfy a migration is not a workaround anybody should be asked for.

The tests below pin three things: a Toss install migrates with no KIS config
present, a KIS install is unchanged, and the guidance in the failure names the
broker actually selected.
"""

import sqlite3
from pathlib import Path

import pytest

import trading.brokers.settings as settings


LEGACY_HOLDINGS = """
CREATE TABLE stock_holdings (
    ticker TEXT, company_name TEXT, buy_price REAL, buy_date TEXT,
    current_price REAL, last_updated TEXT, scenario TEXT, target_price REAL,
    stop_loss REAL, trigger_type TEXT, trigger_mode TEXT, sector TEXT
)
"""


@pytest.fixture
def legacy_db(tmp_path):
    """A pre-multi-account DB: the shape that triggers the migration."""
    conn = sqlite3.connect(tmp_path / "legacy.sqlite")
    conn.execute(LEGACY_HOLDINGS)
    conn.execute(
        "INSERT INTO stock_holdings (ticker, company_name, buy_price, buy_date) "
        "VALUES ('005930', '삼성전자', 70000, '2026-08-01')"
    )
    conn.commit()
    return conn


def _select_toss(monkeypatch, tmp_path, **overrides):
    """Point the whole settings module at a Toss config that exists here."""
    monkeypatch.setenv("PRISM_BROKER", "toss")
    monkeypatch.setenv("PRISM_TRADING_MODE", "demo")

    config = {
        "client_id": "c_test",
        "client_secret": "s_test",
        "account_seq": "1234567",
        **overrides,
    }
    monkeypatch.setattr(settings, "load_toss_config", lambda *a, **k: dict(config))
    # The KIS file must look absent even on a developer machine that has one,
    # or the test would pass for the wrong reason.
    monkeypatch.setattr(settings, "KIS_CONFIG_FILE", tmp_path / "absent-kis.yaml")


def _forbidden_loader():
    raise AssertionError(
        "kis_auth was loaded on a Toss install — importing it reads "
        "kis_devlp.yaml, which is the whole thing being avoided."
    )


def test_toss_scope_comes_from_toss_config(monkeypatch, tmp_path):
    """The account identity is the Toss account, and KIS is never consulted."""
    _select_toss(monkeypatch, tmp_path)

    account_key, name, product, mode = settings.primary_account_scope(
        "kr", kis_auth_loader=_forbidden_loader
    )

    assert account_key == "vps:1234567:01"
    assert name == "toss-primary"
    assert (product, mode) == ("01", "demo")


def test_toss_account_key_keeps_the_three_part_shape(monkeypatch, tmp_path):
    """Consumers split this value, so its shape is part of the contract.

    `stock_tracking_agent._safe_account_log_label` splits on ":" and masks the
    middle field. A two-part key would leak an unmasked account into the logs.
    """
    _select_toss(monkeypatch, tmp_path)

    account_key, *_ = settings.primary_account_scope("kr", kis_auth_loader=_forbidden_loader)

    assert len(account_key.split(":")) == 3


def test_real_mode_is_distinguishable_from_demo(monkeypatch, tmp_path):
    """Dry-run bookkeeping must not be filed under the same key as live trades."""
    _select_toss(monkeypatch, tmp_path)
    monkeypatch.setenv("PRISM_TRADING_MODE", "real")

    demo_key = "vps:1234567:01"
    account_key, _name, _product, mode = settings.primary_account_scope(
        "kr", kis_auth_loader=_forbidden_loader
    )

    assert mode == "real"
    assert account_key != demo_key


def test_a_toss_install_without_account_seq_is_told_about_toss(monkeypatch, tmp_path):
    """The error has to name the file the operator actually owns."""
    _select_toss(monkeypatch, tmp_path, account_seq="")

    with pytest.raises(settings.BrokerConfigError) as excinfo:
        settings.primary_account_scope("kr", kis_auth_loader=_forbidden_loader)

    message = str(excinfo.value)
    assert "toss_config.yaml" in message or "TOSS_ACCOUNT_SEQ" in message
    assert "kis_devlp.yaml" not in message


def test_the_config_hint_points_a_toss_user_at_toss(monkeypatch, tmp_path):
    _select_toss(monkeypatch, tmp_path)

    hint = settings.broker_config_hint()

    assert "toss_config.yaml" in hint
    assert "kis_devlp.yaml" not in hint


def test_the_config_hint_still_points_a_kis_user_at_kis(monkeypatch):
    """Checked without the Toss fixture, which redirects KIS_CONFIG_FILE."""
    monkeypatch.setenv("PRISM_BROKER", "kis")

    assert "kis_devlp.yaml" in settings.broker_config_hint()


def test_toss_migration_stamps_rows_without_any_kis_config(monkeypatch, tmp_path, legacy_db):
    """The end-to-end claim: migrate a legacy DB with no kis_devlp.yaml at all."""
    _select_toss(monkeypatch, tmp_path)

    import tracking.db_schema as schema

    monkeypatch.setattr(
        schema,
        "_get_primary_account_scope",
        lambda: settings.primary_account_scope("kr", kis_auth_loader=_forbidden_loader)[:2],
    )
    schema.create_all_tables(legacy_db.cursor(), legacy_db)

    row = legacy_db.execute(
        "SELECT account_key, account_name, ticker FROM stock_holdings"
    ).fetchone()

    assert row == ("vps:1234567:01", "toss-primary", "005930")


def test_kis_migration_is_unchanged(monkeypatch, legacy_db):
    """A KIS install must still get its KIS account, exactly as before."""
    monkeypatch.setenv("PRISM_BROKER", "kis")

    import tracking.db_schema as schema

    calls = []

    def fake_scope():
        calls.append(True)
        return "vps:87654321:01", "모의-메인"

    monkeypatch.setattr(schema, "_get_primary_account_scope", fake_scope)
    schema.create_all_tables(legacy_db.cursor(), legacy_db)

    assert calls, "the migration never asked for an account scope"
    row = legacy_db.execute(
        "SELECT account_key, account_name FROM stock_holdings"
    ).fetchone()
    assert row == ("vps:87654321:01", "모의-메인")
