"""
HSBC transaction parser — HTML string in, Transaction list out.

No Playwright dependency. Test with fixture HTML files::

    transactions = parse_transaction_table(
        open("tests/fixtures/rewards_page.html").read()
    )

Strategies applied in order:
  0. HSBC-specific #transaction-table
  1. Generic HTML tables with header detection
  2. Styled list/card elements
  3. Text regex fallback
"""

import logging
import re
from datetime import datetime
from typing import List, Optional, Set

from bs4 import BeautifulSoup

from .models import Transaction

logger = logging.getLogger(__name__)


def parse_description_field(raw: str) -> tuple[str, str]:
    """
    Parse a description field that may contain an embedded date.

    'Mar 20, 2026 - PurchaseANTHROPIC'       → ('Mar 20, 2026', 'ANTHROPIC')
    'Mar 20, 2026 - Purchase Return REFUND'   → ('Mar 20, 2026', 'REFUND')
    'SOME PLAIN DESCRIPTION'                  → ('', 'SOME PLAIN DESCRIPTION')
    """
    m = re.match(
        r'^(\w+ \d+, \d{4}) - (?:Purchase Return|Purchase|Fee|Other)?(.*)$',
        raw,
    )
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", raw


def parse_transaction_table(
    html: str,
    scraped_at: Optional[str] = None,
) -> List[Transaction]:
    """
    Extract transactions from an HTML string.

    Returns a deduplicated list of Transaction objects.
    Caller is responsible for cross-call deduplication (e.g. during pagination).
    """
    if scraped_at is None:
        scraped_at = datetime.now().isoformat()

    soup = BeautifulSoup(html, "html.parser")
    seen_keys: Set[str] = set()
    transactions: List[Transaction] = []

    # --- Strategy 0: HSBC-specific #transaction-table ---
    table = soup.select_one("#transaction-table")
    if table:
        rows = table.find_all("tr")
        for row in rows[1:]:  # skip header
            cells = row.find_all("td")
            if len(cells) < 3:
                continue
            raw_desc = cells[0].get_text(strip=True)
            if not raw_desc:
                continue

            raw_date = ""
            if "Pay with your Points" in raw_desc or "Redeem" in raw_desc:
                raw_desc = "[Redemption] " + raw_desc.split("Redeem")[0].strip()
            else:
                raw_date, raw_desc = parse_description_field(raw_desc)

            amount = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            points = cells[4].get_text(strip=True) if len(cells) > 4 else ""
            dedup_key = f"{raw_date}|{raw_desc}|{amount}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)
            transactions.append(Transaction(
                date=raw_date,
                description=raw_desc,
                amount=amount,
                currency="USD",
                points=points,
                card_last_four="",
                scraped_at=scraped_at,
            ))
        if transactions:
            logger.info("Strategy 0 (#transaction-table): %d transactions", len(transactions))

    # --- Strategy 1: Generic HTML tables with header detection ---
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [
            th.get_text(strip=True).lower()
            for th in rows[0].find_all(["th", "td"])
        ]
        logger.debug("Table headers: %s", headers)

        date_idx = _find_column(headers, ["date", "trans date", "transaction date"])
        desc_idx = _find_column(
            headers, ["description", "merchant", "details", "transaction"]
        )
        amount_idx = _find_column(headers, ["amount", "value", "charge"])
        points_idx = _find_column(
            headers, ["total", "points", "reward", "earned", "bonus"]
        )

        if points_idx == -1:
            logger.debug("No points column in headers: %s", headers)
            continue

        logger.info(
            "Column mapping — date:%d desc:%d amount:%d points:%d",
            date_idx, desc_idx, amount_idx, points_idx,
        )

        for row in rows[1:]:
            cells = row.find_all("td")
            max_needed = max(
                i for i in [date_idx, desc_idx, amount_idx, points_idx] if i >= 0
            )
            if len(cells) <= max_needed:
                continue

            raw_desc = _cell_text(cells, desc_idx)
            raw_date = _cell_text(cells, date_idx)

            date_in_desc, desc_from_embedded = parse_description_field(raw_desc)
            if date_in_desc:
                raw_date = date_in_desc
                raw_desc = desc_from_embedded

            raw_amount = _cell_text(cells, amount_idx)
            dedup_key = f"{raw_date}|{raw_desc}|{raw_amount}"
            if dedup_key in seen_keys:
                continue
            seen_keys.add(dedup_key)

            txn = Transaction(
                date=raw_date,
                description=raw_desc,
                amount=raw_amount,
                currency="USD",
                points=_cell_text(cells, points_idx),
                card_last_four="",
                scraped_at=scraped_at,
            )
            if txn.description and txn.description != "N/A":
                transactions.append(txn)

    if transactions:
        logger.info("Strategies 0+1: %d transactions total", len(transactions))
        return transactions

    # --- Strategy 2: Styled list/card elements ---
    _CLASS_PATTERNS = [
        "transaction-row", "activity-row", "reward-row",
        "transaction-item", "activity-item", "txn-row", "statement-line",
    ]
    for tag in soup.find_all(True):
        tag_classes = " ".join(tag.get("class", []))
        data_testid = tag.get("data-testid", "")
        if not any(p in tag_classes for p in _CLASS_PATTERNS) and \
           not any(p in data_testid for p in ["transaction", "activity"]):
            continue

        def _find_text(el, patterns):
            for pat in patterns:
                found = el.find(class_=re.compile(pat))
                if found:
                    return found.get_text(strip=True)
            return ""

        txn = Transaction(
            date=_find_text(tag, ["date"]),
            description=(
                _find_text(tag, ["desc", "merchant", "name"])
                or tag.get_text(strip=True)[:100]
            ),
            amount=_find_text(tag, ["amount", "value"]),
            currency="USD",
            points=_find_text(tag, ["point", "reward", "earn"]) or "N/A",
            card_last_four="",
            scraped_at=scraped_at,
        )
        if txn.description:
            transactions.append(txn)

    if transactions:
        logger.info("Strategy 2 (cards): %d transactions", len(transactions))
        return transactions

    # --- Strategy 3: Text regex fallback ---
    body_text = soup.body.get_text() if soup.body else ""
    date_pattern = re.compile(
        r"(\d{1,2}/\d{1,2}/\d{2,4})\s+"
        r"(.+?)\s+"
        r"\$?([\d,]+\.?\d*)\s+"
        r"([\d,]+)\s*(?:pts?|points?)",
        re.IGNORECASE,
    )
    for match in date_pattern.findall(body_text):
        transactions.append(Transaction(
            date=match[0],
            description=match[1].strip(),
            amount=f"${match[2]}",
            currency="USD",
            points=match[3],
            card_last_four="",
            scraped_at=scraped_at,
        ))

    if transactions:
        logger.info("Strategy 3 (text regex): %d transactions", len(transactions))

    return transactions


def _find_column(headers: List[str], keywords: List[str]) -> int:
    """Find column index matching any keyword, in keyword priority order."""
    for kw in keywords:
        for i, h in enumerate(headers):
            if kw in h:
                return i
    return -1


def _cell_text(cells, idx: int) -> str:
    if idx < 0 or idx >= len(cells):
        return "N/A"
    return cells[idx].get_text(strip=True)
