"""
Google Sheets sync module.

Syncs scraped HSBC credit card transactions with points data to a Google Sheet.
Uses gspread with service account authentication.

Sheet format:
| Date | Description | Amount | Currency | Points | Card Last 4 | Scraped At |
"""

import logging
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from .config import Config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADERS = [
    "Date",
    "Description",
    "Amount",
    "Currency",
    "Points",
    "Card Last 4",
    "Scraped At",
    "Sync Time",
]


def get_sheets_client() -> gspread.Client:
    """Create authenticated gspread client."""
    creds = Credentials.from_service_account_file(
        Config.GOOGLE_CREDENTIALS_PATH, scopes=SCOPES
    )
    return gspread.authorize(creds)


def ensure_headers(worksheet):
    """Ensure the worksheet has proper headers."""
    existing = worksheet.row_values(1)
    if existing != HEADERS:
        worksheet.update("A1:H1", [HEADERS])
        worksheet.format("A1:H1", {"textFormat": {"bold": True}})
        logger.info("Headers set up")


def get_existing_transactions(worksheet) -> set[tuple[str, str, str]]:
    """
    Get set of (date, description, amount) tuples for existing transactions.
    Used to avoid duplicates.
    """
    all_records = worksheet.get_all_values()
    existing = set()
    for row in all_records[1:]:  # Skip header
        if len(row) >= 3:
            existing.add((row[0], row[1], row[2]))
    return existing


def sync_to_sheets(transactions: list[dict]) -> int:
    """
    Sync transactions to Google Sheet. Returns count of new rows added.

    Deduplicates by (date, description, amount) to avoid inserting
    the same transaction twice across multiple runs.
    """
    if not transactions:
        logger.info("No transactions to sync")
        return 0

    client = get_sheets_client()
    spreadsheet = client.open_by_key(Config.GOOGLE_SHEETS_ID)

    # Use first worksheet or create one named "HSBC Points"
    try:
        worksheet = spreadsheet.worksheet("HSBC Points")
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet("HSBC Points", rows=1000, cols=10)
        logger.info("Created 'HSBC Points' worksheet")

    ensure_headers(worksheet)
    existing = get_existing_transactions(worksheet)

    sync_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_rows = []

    for txn in transactions:
        key = (txn["date"], txn["description"], txn["amount"])
        if key not in existing:
            new_rows.append([
                txn["date"],
                txn["description"],
                txn["amount"],
                txn.get("currency", ""),
                txn["points"],
                txn.get("card_last_four", ""),
                txn.get("scraped_at", ""),
                sync_time,
            ])
            existing.add(key)  # Prevent dupes within same batch

    if new_rows:
        # Append all new rows at once
        worksheet.append_rows(new_rows, value_input_option="USER_ENTERED")
        logger.info(f"Added {len(new_rows)} new transactions to Google Sheet")
    else:
        logger.info("No new transactions to add (all already exist)")

    return len(new_rows)
