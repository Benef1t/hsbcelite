#!/usr/bin/env python3
"""
One-time cleanup script: removes duplicate rows from the HSBC Points Google Sheet.

Deduplicates by (date, description, amount) — the same key used by sheets.py —
keeping only the FIRST occurrence of each unique combination.

Usage (from the hsbcelite directory):
    source .venv/bin/activate   # on macOS/Linux
    python cleanup_duplicates.py
"""

import sys
from collections import Counter

import gspread
from google.oauth2.service_account import Credentials

from src.config import Config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def main():
    print("Connecting to Google Sheets...")
    creds = Credentials.from_service_account_file(Config.GOOGLE_CREDENTIALS_PATH, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(Config.GOOGLE_SHEETS_ID)

    sheet = spreadsheet.worksheet("HSBC Points")
    all_rows = sheet.get_all_values()

    if len(all_rows) < 2:
        print("Sheet has no data rows. Nothing to do.")
        return

    header = all_rows[0]
    data_rows = all_rows[1:]

    print(f"Total data rows before cleanup: {len(data_rows)}")

    # Identify duplicates by (date, description, amount)
    keys = [(r[0], r[1], r[2]) for r in data_rows if len(r) >= 3]
    counter = Counter(keys)
    dup_keys = {k for k, v in counter.items() if v > 1}

    if not dup_keys:
        print("No duplicates found! Sheet is already clean.")
        return

    print(f"\nFound {len(dup_keys)} duplicate key(s):")
    for key in sorted(dup_keys):
        print(f"  {counter[key]}x  {key[0]} | {key[1]} | {key[2]}")

    # Keep only first occurrence of each key
    seen = set()
    unique_rows = []
    removed = 0
    for row in data_rows:
        key = (row[0], row[1], row[2]) if len(row) >= 3 else ("", "", "")
        if key not in seen:
            seen.add(key)
            unique_rows.append(row)
        else:
            removed += 1

    print(f"\nRemoving {removed} duplicate row(s)...")

    # Rewrite sheet: clear + write header + unique data rows
    sheet.clear()
    all_clean = [header] + unique_rows
    sheet.update(
        f"A1:{chr(ord('A') + len(header) - 1)}{len(all_clean)}",
        all_clean,
        value_input_option="RAW",
    )

    # Re-apply bold header
    sheet.format("A1:H1", {"textFormat": {"bold": True}})

    print(f"Done! Removed {removed} duplicate(s). Sheet now has {len(unique_rows)} data rows.")


if __name__ == "__main__":
    main()
