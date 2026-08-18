"""The Toss US order path, end to end (migration audit Phase 2.5).

Before this, no Toss US order could complete: `execute_sell(ticker=...)` never
reached `TossBroker.async_sell_stock(stock_code, ...)`, and if it had, the
Decimal quantity in the fill could not be bound into the intent ledger. Both
failures landed after the holdings row had already been deleted, so a refused
order left no record of the position at all.
"""

import asyncio
import sqlite3
from decimal import Decimal

import pytest

from prism_core.order_intents import IntentStore, OrderIntent


def _intent(quantity, key="p1"):
    return OrderIntent.create(
        market="US", account_id="acct", symbol="AAPL", side="SELL",
        order_style="market", source="test", source_position_id=key,
        quantity=quantity,
    )


# ── The instrument reaches the broker ────────────────────────────────────────


def test_toss_accepts_the_instrument_positionally():
    """The call shape the US agent now uses must reach `_sell`."""
    from trading.brokers.toss.adapter import TossBroker

    broker = TossBroker(client=object(), market="US")
    reached = {}
    broker._sell = lambda code, limit, qty: reached.update(
        code=code, limit=limit, qty=qty
    ) or {"success": True}

    asyncio.run(broker.async_sell_stock("AAPL", limit_price=100, quantity=1))
    assert reached == {"code": "AAPL", "limit": 100, "qty": 1}


def test_the_keyword_call_that_used_to_be_made_still_fails_loudly():
    """Guard the diagnosis: `ticker=` is not silently absorbed by an alias.

    An alias would paper over a BrokerPort violation; the contract says the
    instrument travels positionally, so the keyword form must keep raising.
    """
    from trading.brokers.toss.adapter import TossBroker

    broker = TossBroker(client=object(), market="US")
    with pytest.raises(TypeError):
        asyncio.run(broker.async_sell_stock(ticker="AAPL", limit_price=1, quantity=1))


# ── Fractional quantities survive the intent ledger ──────────────────────────


def test_a_fractional_fill_is_recorded_instead_of_going_unknown(tmp_path):
    """record_result binds the broker's own quantity; Toss US reports Decimal."""
    store = IntentStore(tmp_path / "intents.sqlite")
    intent = _intent(Decimal("0.84"))
    reserved, info = store.reserve(intent)
    assert reserved, info
    store.mark_submitting(intent.id)

    store.record_result(
        intent, status="SUBMITTED", accepted=True, broker="toss",
        response={"quantity": Decimal("0.84"), "price": Decimal("100.5")},
    )

    conn = sqlite3.connect(tmp_path / "intents.sqlite")
    assert conn.execute(
        "SELECT typeof(submitted_quantity), submitted_quantity FROM broker_orders"
    ).fetchall() == [("text", "0.84")]


def test_quantity_columns_keep_the_exact_decimal(tmp_path):
    """INTEGER affinity rewrote '0.84' as a binary float; TEXT does not."""
    store = IntentStore(tmp_path / "intents.sqlite")
    store.reserve(_intent(Decimal("0.788569")))

    conn = sqlite3.connect(tmp_path / "intents.sqlite")
    assert conn.execute(
        "SELECT typeof(quantity), quantity FROM order_intents"
    ).fetchall() == [("text", "0.788569")]


def test_whole_share_counts_stay_integers(tmp_path):
    assert _intent(Decimal("3")).quantity == 3
    assert _intent(7).quantity == 7
    assert _intent(None).quantity is None


def test_the_migration_survives_a_populated_child_table(tmp_path):
    """broker_orders holds a FOREIGN KEY into order_intents, and _connect turns
    foreign_keys ON — under which SQLite refuses DROP TABLE outright. Without
    the documented rebuild procedure the migration failed on every real
    database, left a stale __new copy behind, and re-ran on every open."""
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE order_intents (
            id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
            market TEXT NOT NULL, account_id TEXT NOT NULL, symbol TEXT NOT NULL,
            side TEXT NOT NULL, order_style TEXT NOT NULL, quantity INTEGER,
            cash_amount TEXT, limit_price TEXT, reason TEXT, source TEXT NOT NULL,
            source_decision_id TEXT, source_position_id TEXT,
            execution_mode TEXT NOT NULL, status TEXT NOT NULL, error_type TEXT,
            error_message TEXT, raw_request_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, submitted_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE broker_orders (
            id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, broker TEXT NOT NULL,
            broker_order_id TEXT, accepted INTEGER NOT NULL, status TEXT NOT NULL,
            submitted_quantity INTEGER, submitted_price TEXT, raw_code TEXT,
            raw_message TEXT, raw_response_json TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            FOREIGN KEY(intent_id) REFERENCES order_intents(id))"""
    )
    conn.execute(
        "INSERT INTO order_intents VALUES ('i1','k','US','a','AAPL','SELL',"
        "'market',1,NULL,NULL,NULL,'s',NULL,'p','live','CREATED',NULL,NULL,"
        "'{}','t','t',NULL)"
    )
    conn.execute(
        "INSERT INTO broker_orders VALUES "
        "('b1','i1','toss','X',1,'SUBMITTED',1,'10',NULL,NULL,'{}','t')"
    )
    conn.commit()
    conn.close()

    IntentStore(path)

    conn = sqlite3.connect(path)
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "order_intents__new" not in tables, "stale rebuild copy left behind"
    for table, column in (("order_intents", "quantity"),
                          ("broker_orders", "submitted_quantity")):
        declared = [
            row[2] for row in conn.execute(f"PRAGMA table_info({table})")
            if row[1] == column
        ]
        assert declared == ["TEXT"], f"{table}.{column} was not widened"
    assert conn.execute("SELECT id FROM order_intents").fetchall() == [("i1",)]
    assert conn.execute("SELECT id FROM broker_orders").fetchall() == [("b1",)]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_a_legacy_integer_column_is_widened_without_losing_rows(tmp_path):
    """Databases predating fractional shares declare quantity INTEGER."""
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE order_intents (
            id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
            market TEXT NOT NULL, account_id TEXT NOT NULL, symbol TEXT NOT NULL,
            side TEXT NOT NULL, order_style TEXT NOT NULL, quantity INTEGER,
            cash_amount TEXT, limit_price TEXT, reason TEXT, source TEXT NOT NULL,
            source_decision_id TEXT, source_position_id TEXT,
            execution_mode TEXT NOT NULL, status TEXT NOT NULL, error_type TEXT,
            error_message TEXT, raw_request_json TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, submitted_at TEXT)"""
    )
    conn.execute(
        "INSERT INTO order_intents VALUES ('old','k','KR','a','005930','BUY',"
        "'market',7,NULL,NULL,NULL,'s',NULL,'p','live','CREATED',NULL,NULL,"
        "'{}','t','t',NULL)"
    )
    conn.commit()
    conn.close()

    IntentStore(path)  # ensure_schema runs the migration

    conn = sqlite3.connect(path)
    declared = [
        row[2] for row in conn.execute("PRAGMA table_info(order_intents)")
        if row[1] == "quantity"
    ]
    assert declared == ["TEXT"]
    assert conn.execute("SELECT id FROM order_intents").fetchall() == [("old",)]
    indexes = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx%'"
        )
    }
    assert "idx_order_intents_status" in indexes


# ── A zero split must not liquidate the position ─────────────────────────────


def test_zero_split_refuses_rather_than_selling_everything():
    from trading.brokers.toss.adapter import TossBroker

    broker = TossBroker(client=object(), market="US")
    broker.get_holding_quantity_checked = lambda s: ("HELD", Decimal("1.68"))
    submitted = []
    broker._submit = lambda *a, **k: submitted.append(k) or {"success": True}

    outcome = broker._sell("AAPL", None, Decimal("0.000000"))
    assert outcome["success"] is False
    assert "refusing full-liquidation" in outcome["message"]
    assert not submitted
