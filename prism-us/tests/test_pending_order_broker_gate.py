"""The reserved-order batch must recognise a non-KIS broker before it acts.

Found by running the batch for real on a Toss install: it died with
"no such table: us_pending_orders" — a table only the KIS queueing path
creates — instead of taking the skip its own comment describes.

Two defects stacked. The guard sat after the database work, and the guard
itself never worked: sys.path puts prism-us first, so `trading` resolves to
prism-us/trading, which has no `brokers` package, and the except beneath the
import defaulted to "kis". A Toss install ran the whole KIS path.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "us_pending_order_batch.py"


def _load():
    spec = importlib.util.spec_from_file_location("us_pending_order_batch_under_test", MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_broker_is_actually_resolved(monkeypatch):
    """The plain import used to fail here, silently answering "kis"."""
    monkeypatch.setenv("PRISM_BROKER", "toss")
    module = _load()

    assert module._selected_broker() == "toss"


def test_a_toss_install_skips_before_touching_the_database(monkeypatch):
    monkeypatch.setenv("PRISM_BROKER", "toss")
    module = _load()

    def _no_db(*args, **kwargs):
        raise AssertionError("the database was opened despite a non-KIS broker")

    monkeypatch.setattr(module.sqlite3, "connect", _no_db)

    module.process_pending_orders()  # must return quietly


def test_an_unreadable_broker_config_skips_rather_than_ordering(monkeypatch):
    """Draining a queue late is recoverable; placing KIS orders for an operator
    who moved to Toss is not."""
    module = _load()
    monkeypatch.setattr(
        module, "_selected_broker", lambda: "unknown"
    )

    def _no_db(*args, **kwargs):
        raise AssertionError("the database was opened on an unknown broker")

    monkeypatch.setattr(module.sqlite3, "connect", _no_db)

    module.process_pending_orders()
