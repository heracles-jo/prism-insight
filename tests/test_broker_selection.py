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


# ── Direct trader factories ──────────────────────────────────────────────────


def test_the_trader_factories_default_to_kis(monkeypatch):
    """Asserts the routing choice, not a live trader — constructing one would
    authenticate against KIS and turn a unit test into a network call."""
    import trading.domestic_stock_trading as kis_module
    from trading.brokers.factory import domestic_trader

    built = {}

    def fake_trader(**kwargs):
        built.update(kwargs)
        return "kis-trader"

    monkeypatch.setattr(kis_module, "DomesticStockTrading", fake_trader)

    assert domestic_trader(mode="demo", account_name="primary") == "kis-trader"
    assert built == {"mode": "demo", "account_name": "primary"}


def test_the_trader_factories_follow_the_broker_setting(monkeypatch, tmp_path):
    """The gap this closes: callers that want a trader, not a context."""
    from trading.brokers.factory import domestic_trader, us_trader
    from trading.brokers.toss.adapter import TossBroker

    monkeypatch.setenv("PRISM_BROKER", "toss")
    monkeypatch.setenv("TOSS_CLIENT_ID", "c_abc")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "s_xyz")
    monkeypatch.setattr(config, "TOSS_CONFIG_FILE", tmp_path / "absent.yaml")

    kr = domestic_trader(mode="demo", account_name="primary", product_code="01")
    us = us_trader(mode="demo", account_name="primary", product_code="01")

    assert isinstance(kr, TossBroker) and kr.market == "KR"
    assert isinstance(us, TossBroker) and us.market == "US"


def test_kis_only_keywords_do_not_break_the_toss_path(monkeypatch, tmp_path):
    """account_name/product_code are meaningless to Toss but callers pass them."""
    from trading.brokers.factory import domestic_trader

    monkeypatch.setenv("PRISM_BROKER", "toss")
    monkeypatch.setenv("TOSS_CLIENT_ID", "c_abc")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "s_xyz")
    monkeypatch.setattr(config, "TOSS_CONFIG_FILE", tmp_path / "absent.yaml")

    assert domestic_trader(
        mode="demo", account_name="x", product_code="01", auto_trading=False
    ) is not None


def test_demo_mode_traders_never_hold_a_live_client(monkeypatch, tmp_path):
    from trading.brokers.factory import domestic_trader
    from trading.brokers.toss.dryrun import DryRunTossClient

    monkeypatch.setenv("PRISM_BROKER", "toss")
    monkeypatch.setenv("PRISM_TRADING_MODE", "demo")
    monkeypatch.setenv("TOSS_CLIENT_ID", "c_abc")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "s_xyz")
    monkeypatch.setattr(config, "TOSS_CONFIG_FILE", tmp_path / "absent.yaml")

    assert isinstance(domestic_trader().client, DryRunTossClient)


# ── Stance quote provider ────────────────────────────────────────────────────


def test_the_quote_provider_follows_the_broker_setting(monkeypatch, tmp_path):
    """Trading on Toss while quoting from KIS is the mismatch this prevents."""
    from prism_core.stance_quotes import TossQuoteProvider, quote_provider_for_broker

    monkeypatch.setenv("PRISM_BROKER", "toss")
    monkeypatch.setenv("TOSS_CLIENT_ID", "c_abc")
    monkeypatch.setenv("TOSS_CLIENT_SECRET", "s_xyz")
    monkeypatch.setattr(config, "TOSS_CONFIG_FILE", tmp_path / "absent.yaml")

    assert isinstance(quote_provider_for_broker("demo"), TossQuoteProvider)


def test_the_toss_quote_provider_reports_limits_and_halts():
    """Reusing KisQuoteProvider would leave these always-False / always-True."""
    from decimal import Decimal

    from prism_core.stance_quotes import TossQuoteProvider

    class Stub:
        def request(self, method, path, *, params=None, **kwargs):
            if path.startswith("/api/v1/prices"):
                return [{"symbol": "005930", "lastPrice": "93000"}]
            if path.startswith("/api/v1/price-limits"):
                return {"upperLimitPrice": "93000", "lowerLimitPrice": "50400"}
            if path.startswith("/api/v1/stocks"):
                return [{"symbol": "005930", "status": "ACTIVE",
                         "koreanMarketDetail": {"krxTradingSuspended": True}}]
            raise AssertionError(path)

    quote = TossQuoteProvider(Stub())("KRX", "005930")
    assert quote.price == Decimal("93000")
    assert quote.at_upper_limit is True
    assert quote.at_lower_limit is False
    assert quote.tradable is False


def test_a_failed_limit_lookup_does_not_assert_a_limit():
    from prism_core.stance_quotes import TossQuoteProvider

    class Stub:
        def request(self, method, path, *, params=None, **kwargs):
            if path.startswith("/api/v1/prices"):
                return [{"symbol": "005930", "lastPrice": "70000"}]
            raise RuntimeError("down")

    quote = TossQuoteProvider(Stub())("KRX", "005930")
    assert quote.at_upper_limit is False and quote.at_lower_limit is False
    assert quote.tradable is True  # unknown must not block a declaration


# ── Tripwire ─────────────────────────────────────────────────────────────────


def test_no_production_code_constructs_a_kis_trader_directly():
    """This is the check whose absence let broker selection be half-wired.

    Building `DomesticStockTrading` / `USStockTrading` by hand bypasses
    `PRISM_BROKER` entirely, which produced orders on one broker and balance
    reports from the other. New call sites must go through the factory.

    The allowlist is for code that is legitimately KIS-specific — the KIS market
    data source, the KIS snapshot helper, the factory itself, and the reserved
    order batch, which drains a queue only KIS can create.
    """
    import re
    import subprocess

    allowed = {
        "trading/brokers/factory.py",          # the factory itself
        "cores/market_data/kis_source.py",     # *is* the KIS source
        "cores/kis_market_snapshot.py",        # KIS-specific by name
        "prism-us/us_pending_order_batch.py",  # guarded: KIS-only mechanism
        "prism_core/stance_quotes.py",         # KIS provider construction
    }

    out = subprocess.run(
        ["git", "grep", "-nE",
         r"\b(DomesticStockTrading|USStockTrading|MultiAccountDomesticStockTrading|"
         r"MultiAccountUSStockTrading)\("],
        capture_output=True, text=True,
    ).stdout

    offenders = []
    for line in out.splitlines():
        path = line.split(":", 1)[0]
        if path in allowed:
            continue
        if path.startswith(("tests/", "prism-us/tests/", "examples/messaging/")):
            continue
        if path in ("trading/domestic_stock_trading.py", "prism-us/trading/us_stock_trading.py"):
            continue  # the definitions themselves
        if re.search(r"^\s*(#|\*|\"|')", line.split(":", 2)[-1]):
            continue  # comments and docstrings
        offenders.append(line)

    assert not offenders, (
        "these construct a KIS trader directly and so ignore PRISM_BROKER:\n  "
        + "\n  ".join(offenders)
    )
