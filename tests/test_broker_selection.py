"""Broker selection: does one environment variable actually switch everything?

That is the hypothesis this whole effort rests on, so it is tested directly:
with PRISM_BROKER unset the KIS path must be constructed exactly as before, and
with it set to toss the same `ExecutionService.domestic()` call must yield a
Toss trader — with no caller passing anything different.

The safety defaults get their own tests. Toss has no paper-trading server, so
the mode default is the difference between a simulation and real money, and a
typo must not be what decides it.
"""

import pytest

from trading.brokers import settings as config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "PRISM_BROKER", "PRISM_TRADING_MODE", "TOSS_CLIENT_ID",
        "TOSS_CLIENT_SECRET", "TOSS_ACCOUNT_SEQ", "TOSS_BASE_URL",
        "PRISM_BUY_AMOUNT_KRW", "PRISM_BUY_AMOUNT_USD",
    ):
        monkeypatch.delenv(name, raising=False)
    yield


# ── Selection ────────────────────────────────────────────────────────────────


def test_the_default_broker_is_kis():
    """Silence must mean no change for every existing installation."""
    assert config.selected_broker() == "kis"


def test_the_broker_can_be_switched_to_toss(monkeypatch):
    monkeypatch.setenv("PRISM_BROKER", "toss")
    assert config.selected_broker() == "toss"


@pytest.mark.parametrize("value", ["TOSS", "  toss  ", "Kis"])
def test_broker_selection_tolerates_case_and_whitespace(monkeypatch, value):
    monkeypatch.setenv("PRISM_BROKER", value)
    assert config.selected_broker() in {"kis", "toss"}


def test_an_unknown_broker_fails_loudly(monkeypatch):
    """Silently falling back would route orders somewhere unintended."""
    monkeypatch.setenv("PRISM_BROKER", "kiwoom")
    with pytest.raises(config.BrokerConfigError):
        config.selected_broker()


# ── Mode, where the default is a safety decision ─────────────────────────────


def test_the_default_mode_is_demo():
    assert config.trading_mode() == "demo"
    assert config.is_demo() is True


def test_real_mode_must_be_asked_for_explicitly(monkeypatch):
    monkeypatch.setenv("PRISM_TRADING_MODE", "real")
    assert config.trading_mode() == "real"
    assert config.is_demo() is False


@pytest.mark.parametrize("typo", ["prod", "live", "REEL", ""])
def test_an_unrecognised_mode_falls_back_to_demo(monkeypatch, typo):
    """Toss has no paper server; a typo must not authorise real money."""
    monkeypatch.setenv("PRISM_TRADING_MODE", typo)
    assert config.trading_mode() == "demo"


# ── Toss configuration ───────────────────────────────────────────────────────


def test_toss_config_reads_the_yaml_file(tmp_path):
    path = tmp_path / "toss_config.yaml"
    path.write_text(
        "client_id: c_abc\nclient_secret: s_xyz\naccount_seq: acc-1\n", encoding="utf-8"
    )

    loaded = config.load_toss_config(path)
    assert loaded["client_id"] == "c_abc"
    assert loaded["account_seq"] == "acc-1"


def test_environment_overrides_the_file(tmp_path, monkeypatch):
    """Lets secrets be injected in a container with no file on disk."""
    path = tmp_path / "toss_config.yaml"
    path.write_text("client_id: from_file\nclient_secret: from_file\n", encoding="utf-8")
    monkeypatch.setenv("TOSS_CLIENT_ID", "from_env")

    assert config.load_toss_config(path)["client_id"] == "from_env"


def test_config_works_with_no_file_at_all(tmp_path, monkeypatch):
    monkeypatch.setenv("TOSS_CLIENT_ID", "c_abc")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "s_xyz")

    assert config.load_toss_config(tmp_path / "absent.yaml")["client_id"] == "c_abc"


def test_missing_credentials_say_how_to_fix_it(tmp_path):
    with pytest.raises(config.BrokerConfigError) as excinfo:
        config.load_toss_config(tmp_path / "absent.yaml")

    message = str(excinfo.value)
    assert "client_id" in message and "client_secret" in message
    assert "example" in message


def test_a_malformed_yaml_file_is_reported_not_ignored(tmp_path):
    path = tmp_path / "toss_config.yaml"
    path.write_text("client_id: [unclosed\n", encoding="utf-8")

    with pytest.raises(config.BrokerConfigError):
        config.load_toss_config(path)


def test_buy_amount_is_read_per_market(monkeypatch):
    monkeypatch.setenv("PRISM_BUY_AMOUNT_KRW", "250000")
    assert config.toss_buy_amount("KR") == 250000
    assert config.toss_buy_amount("US") is None


def test_a_nonnumeric_buy_amount_is_ignored_rather_than_fatal(monkeypatch):
    monkeypatch.setenv("PRISM_BUY_AMOUNT_KRW", "lots")
    assert config.toss_buy_amount("KR") is None


# ── The factory ──────────────────────────────────────────────────────────────


def test_the_kis_path_builds_the_same_context_as_before():
    """Not an equivalent object — the same class the old code constructed."""
    from trading.brokers.factory import domestic_context
    from trading.domestic_stock_trading import AsyncTradingContext

    assert isinstance(domestic_context(account_name="primary"), AsyncTradingContext)


def test_selecting_toss_builds_a_toss_context(monkeypatch):
    from trading.brokers.factory import TossTradingContext, domestic_context

    monkeypatch.setenv("PRISM_BROKER", "toss")
    context = domestic_context(account_name="primary")

    assert isinstance(context, TossTradingContext)
    assert context.market == "KR"


def test_the_us_factory_honours_the_same_switch(monkeypatch):
    from trading.brokers.factory import TossTradingContext, us_context

    monkeypatch.setenv("PRISM_BROKER", "toss")
    assert isinstance(us_context(), TossTradingContext)
    assert us_context().market == "US"


def test_a_toss_context_yields_a_toss_broker_in_demo(monkeypatch, tmp_path):
    """The end-to-end claim: one env var, and the trader is Toss."""
    import asyncio

    from trading.brokers.factory import TossTradingContext
    from trading.brokers.toss.adapter import TossBroker
    from trading.brokers.toss.dryrun import DryRunTossClient

    monkeypatch.setenv("PRISM_BROKER", "toss")
    monkeypatch.setenv("TOSS_CLIENT_ID", "c_abc")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "s_xyz")
    monkeypatch.setattr(config, "TOSS_CONFIG_FILE", tmp_path / "absent.yaml")

    async def exercise():
        async with TossTradingContext(market="KR") as trader:
            return trader

    trader = asyncio.run(exercise())
    assert isinstance(trader, TossBroker)
    assert isinstance(trader.client, DryRunTossClient), "demo mode must not hold a live client"


def test_real_mode_yields_a_live_client(monkeypatch, tmp_path):
    import asyncio

    from trading.brokers.factory import TossTradingContext
    from trading.brokers.toss.client import TossClient

    monkeypatch.setenv("PRISM_BROKER", "toss")
    monkeypatch.setenv("PRISM_TRADING_MODE", "real")
    monkeypatch.setenv("TOSS_CLIENT_ID", "c_abc")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "s_xyz")
    monkeypatch.setattr(config, "TOSS_CONFIG_FILE", tmp_path / "absent.yaml")

    async def exercise():
        async with TossTradingContext(market="KR") as trader:
            return trader

    assert isinstance(asyncio.run(exercise()).client, TossClient)


def test_real_mode_says_so_out_loud(monkeypatch, tmp_path, caplog):
    """Toss has no paper server, so this is worth one loud line per run."""
    import asyncio
    import logging

    from trading.brokers.factory import TossTradingContext

    monkeypatch.setenv("PRISM_TRADING_MODE", "real")
    monkeypatch.setenv("TOSS_CLIENT_ID", "c_abc")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "s_xyz")
    monkeypatch.setattr(config, "TOSS_CONFIG_FILE", tmp_path / "absent.yaml")

    async def exercise():
        async with TossTradingContext(market="KR"):
            return None

    with caplog.at_level(logging.WARNING):
        asyncio.run(exercise())

    assert "REAL" in caplog.text


# ── The ledger label ─────────────────────────────────────────────────────────


def test_the_kis_traders_still_label_rows_kis():
    """They predate the port and have no name; old rows must stay consistent."""
    from trading.brokers.factory import broker_label

    class LegacyKisTrader:
        pass

    assert broker_label(LegacyKisTrader()) == "KIS"


def test_a_named_broker_labels_rows_with_its_own_name():
    from trading.brokers.factory import broker_label
    from trading.brokers.toss.adapter import TossBroker

    assert broker_label(TossBroker(object(), market="KR")) == "TOSS"


def test_classify_result_keeps_its_single_argument_behaviour():
    """Existing callers pass one argument and must keep getting KIS."""
    from prism_core.execution_service import ExecutionService

    assert ExecutionService._classify_result({"success": True}) == ("SUBMITTED", True, "KIS")
    assert ExecutionService._classify_result({"success": False}) == ("FAILED", False, "KIS")


def test_classify_result_labels_with_the_given_broker():
    from prism_core.execution_service import ExecutionService

    assert ExecutionService._classify_result({"success": True}, "TOSS") == (
        "SUBMITTED", True, "TOSS"
    )
    assert ExecutionService._classify_result({"outcome_unknown": True}, "TOSS") == (
        "UNKNOWN", False, "TOSS"
    )


def test_a_locally_queued_order_is_labelled_by_where_it_sits():
    """It never reached any broker, so it is not that broker's row."""
    from prism_core.execution_service import ExecutionService

    queued = {"success": True, "order_no": "PENDING-1"}
    assert ExecutionService._classify_result(queued, "TOSS") == ("QUEUED", True, "LOCAL_QUEUE")


def test_the_execution_service_derives_the_label_from_its_trader():
    from prism_core.execution_service import ExecutionService

    class NamedTrader:
        name = "toss"

    assert ExecutionService(NamedTrader())._broker_label == "TOSS"

    class UnnamedTrader:
        pass

    assert ExecutionService(UnnamedTrader())._broker_label == "KIS"
