#!/usr/bin/env python3
"""Read-only legacy holdings versus Phase 4-a positions comparison."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from prism_core.positions import PositionStore

# Load .env before reading STOCK_TRACKING_DB: a fresh process does not inherit
# it, and an install that sets the override there rather than exporting it would
# otherwise have this tool touch a different database than the agents write to.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass



def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-path",
        default=os.getenv("STOCK_TRACKING_DB")
        or str(Path(__file__).resolve().parents[1] / "stock_tracking_db.sqlite"),
    )
    parser.add_argument(
        "--market",
        choices=("kr", "us", "both"),
        default="both",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    markets = ("KR", "US") if args.market == "both" else (args.market.upper(),)
    try:
        uri = Path(args.db_path).resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            store = PositionStore(connection)
            results = [store.compare_legacy_positions(market) for market in markets]
        finally:
            connection.close()
    except Exception as error:
        print(
            json.dumps(
                {"status": "error", "error_type": type(error).__name__},
                sort_keys=True,
            )
        )
        return 2

    matches = all(result["matches"] for result in results)
    print(
        json.dumps(
            {"status": "ok" if matches else "mismatch", "results": results},
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
