"""Keep broker selection out of the ambient environment during tests.

Three test modules call `load_dotenv()` at import time, which writes the
developer's `.env` into `os.environ` for the whole pytest session. That was
harmless until broker selection became an environment variable: with
`PRISM_BROKER=toss` in a local `.env`, `ExecutionService.domestic()` starts
returning a Toss context and thirteen KIS tests fail on a machine where nothing
about the code has changed.

Tests should assert on code, not on whoever ran them. So the broker variables
are cleared before every test, and a test that cares about a particular broker
sets it explicitly with `monkeypatch.setenv`.

Only the broker variables are cleared. Other tests deliberately load `.env` for
their own settings, and taking that away would break them.
"""

import pytest

_BROKER_ENV = (
    "PRISM_BROKER",
    "PRISM_TRADING_MODE",
    "PRISM_BUY_AMOUNT_KRW",
    "PRISM_BUY_AMOUNT_USD",
    "TOSS_CLIENT_ID",
    "TOSS_CLIENT_SECRET",
    "TOSS_ACCOUNT_SEQ",
    "TOSS_BASE_URL",
)


@pytest.fixture(autouse=True)
def _neutral_broker_env(monkeypatch):
    """Default every test to the unconfigured state: KIS, demo, no Toss."""
    for name in _BROKER_ENV:
        monkeypatch.delenv(name, raising=False)
    yield
