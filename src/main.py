"""
HSBC Credit Card Points Tracker - Main Entry Point

Usage:
    # Run once (scrape + sync)
    python -m src.main

    # Run as a scheduler (checks twice daily)
    python -m src.main --schedule

    # Run in non-headless mode for debugging
    HEADLESS=false python -m src.main

    # Dry run (scrape only, don't sync to sheets)
    python -m src.main --dry-run
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime

import schedule

from .config import Config
from .scraper import scrape_hsbc_points
from .sheets import sync_to_sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("hsbc_points.log"),
    ],
)
logger = logging.getLogger(__name__)


def run_once(dry_run=False):
    """Run a single scrape + sync cycle."""
    logger.info("=" * 50)
    logger.info(f"Starting HSBC points check at {datetime.now()}")
    logger.info("=" * 50)

    try:
        # Scrape
        transactions = scrape_hsbc_points()
        logger.info(f"Scraped {len(transactions)} transactions")

        if not transactions:
            logger.warning("No transactions found. Check screenshots/ for debugging.")
            return

        # Print summary
        for txn in transactions:
            logger.info(
                f"  {txn['date']} | {txn['description'][:40]:40s} | "
                f"{txn['amount']:>10s} | Points: {txn['points']}"
            )

        if dry_run:
            logger.info("Dry run mode - not syncing to Google Sheets")
            print(json.dumps(transactions, indent=2, ensure_ascii=False))
            return

        # Sync to Google Sheets
        new_count = sync_to_sheets(transactions)
        logger.info(f"Sync complete. {new_count} new transactions added.")

    except Exception as e:
        logger.error(f"Error during run: {e}", exc_info=True)


def run_scheduler():
    """Run the scraper on a schedule."""
    interval = Config.CHECK_INTERVAL_MINUTES
    logger.info(f"Starting scheduler - checking every {interval} minutes")

    # Run immediately on start
    run_once()

    # Schedule subsequent runs
    schedule.every(interval).minutes.do(run_once)

    while True:
        schedule.run_pending()
        time.sleep(60)


def main():
    parser = argparse.ArgumentParser(description="HSBC Credit Card Points Tracker")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="Run as a scheduler (default: run once)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape only, don't sync to Google Sheets",
    )
    args = parser.parse_args()

    # Validate config
    errors = Config.validate()
    if errors and not args.dry_run:
        logger.error("Configuration errors:")
        for err in errors:
            logger.error(f"  - {err}")
        logger.error("Please check your .env file. See .env.example for reference.")
        sys.exit(1)

    if args.schedule:
        run_scheduler()
    else:
        run_once(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
