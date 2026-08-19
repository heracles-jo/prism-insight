"""The performance batch must create the tables it reads.

Found by running it for real: it died on

    sqlite3.OperationalError: no such table: us_analysis_performance_tracker

That table was only ever created by us_stock_tracking_agent, and this batch
assumed that had already run against the same database. It is a separate cron
entry, and the tracking agent could not even be imported until the
cores-shadowing fix — so on such an install this batch failed every day.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _tracker(db_path):
    from us_performance_tracker_batch import USPerformanceTrackerBatch

    return USPerformanceTrackerBatch(db_path=str(db_path))


def test_an_empty_database_is_populated_rather_than_fatal(tmp_path):
    db = tmp_path / "fresh.sqlite"
    sqlite3.connect(db).close()  # exists, but has no tables at all

    _tracker(db).ensure_columns_exist()  # used to raise OperationalError

    names = {
        row[0]
        for row in sqlite3.connect(db).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "us_analysis_performance_tracker" in names


def test_running_twice_is_harmless(tmp_path):
    """CREATE TABLE IF NOT EXISTS throughout, so the daily run costs nothing
    once the tables are there — and must not disturb existing rows."""
    db = tmp_path / "twice.sqlite"
    tracker = _tracker(db)
    tracker.ensure_columns_exist()

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO us_analysis_performance_tracker "
        "(ticker, company_name, analysis_date, analysis_price, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("AAPL", "Apple Inc.", "2026-08-19", 230.0, "2026-08-19 09:00:00"),
    )
    conn.commit()
    conn.close()

    tracker.ensure_columns_exist()

    rows = sqlite3.connect(db).execute(
        "SELECT ticker FROM us_analysis_performance_tracker"
    ).fetchall()
    assert rows == [("AAPL",)]
