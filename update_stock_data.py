#!/usr/bin/env python3
"""
Stock information update script

Run periodically to update stock information (codes, names) daily
"""
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

import json
import logging
import argparse
import sys
from datetime import datetime

try:
    from krx_data_client import _get_client
except ImportError:
    print("krx_data_client package is not installed. Install with 'pip install kospi-kosdaq-stock-server'.")
    exit(1)

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("stock_data_update.log")
    ]
)
logger = logging.getLogger(__name__)

def update_stock_data(output_file="stock_map.json"):
    """
    Update stock information

    Args:
        output_file (str): File path to save

    Returns:
        bool: Success status
    """
    try:
        # Today's date
        today = datetime.now().strftime("%Y%m%d")
        logger.info(f"Starting stock data update: {today}")

        # Fetch all stock code-name mappings at once (efficient!)
        logger.info("Fetching all stock information...")
        code_to_name = _fetch_code_to_name()
        logger.info(f"Loaded {len(code_to_name)} stocks")

        # Create reverse mapping
        name_to_code = {name: code for code, name in code_to_name.items()}

        # Save data
        data = {
            "code_to_name": code_to_name,
            "name_to_code": name_to_code,
            "updated_at": datetime.now().isoformat()
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"Stock data update complete: {len(code_to_name)} stocks, file: {output_file}")
        return True
    except Exception as e:
        logger.error(f"Stock data update failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

def _fetch_code_to_name() -> dict:
    """The whole KRX code -> name map, from KRX if it will answer, else FDR.

    KRX permits one session per account, so a login anywhere else invalidates
    this one: the client authenticates, then the data page bounces it back to
    the login form and the run dies. That is not hypothetical — it is what this
    script did every day, and the caller below swallowed it.

Naver carries the same code/name pairs in the bulk rows the screening
    snapshot already reads, and needs no login — which is why the KRX-login data
    was rerouted there in the first place. KRX stays first only because it is
    the registry of record.

    The per-ticker source chain is deliberately not the fallback here: it
    answers one name per call, and this needs a few thousand.
    """
    try:
        client = _get_client()
        return client.get_market_ticker_name(market="ALL")
    except Exception as exc:  # noqa: BLE001 - any KRX failure means try the other one
        logger.warning(f"KRX name map unavailable ({exc}); falling back to Naver")

    from cores.naver_market_snapshot import fetch_naver_ticker_names

    return fetch_naver_ticker_names()


def main():
    parser = argparse.ArgumentParser(description="Update stock information")
    parser.add_argument("--output", default="stock_map.json", help="File path to save")

    args = parser.parse_args()
    # Propagate the result. This used to discard it, so a run that fetched
    # nothing still exited 0 and cron recorded a successful daily update.
    return 0 if update_stock_data(args.output) else 1

if __name__ == "__main__":
    sys.exit(main())