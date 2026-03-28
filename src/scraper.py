"""
HSBC US Credit Card Points Scraper using Playwright.

Targets HSBC US (us.hsbc.com) online banking to scrape credit card
transaction-level rewards points data.

HSBC US login flow:
1. Go to https://www.us.hsbc.com/online-banking/
2. Enter username → click "Continue" (or username + password on same page)
3. Enter password → click "Log on"
4. Navigate to credit card rewards/points section
5. Scrape per-transaction points from the rewards activity table

The scraper saves screenshots at each step for debugging.
If selectors break due to HSBC redesign, check screenshots/ and update.
"""

import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from .config import Config

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = Path("screenshots")
COOKIES_FILE = Path("credentials/cookies.json")


@dataclass
class Transaction:
    date: str
    description: str
    amount: str
    currency: str
    points: str
    card_last_four: str
    scraped_at: str


def save_cookies(context):
    """Save browser cookies for session reuse."""
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    cookies = context.cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    logger.info("Cookies saved for session reuse")


def load_cookies(context):
    """Load previously saved cookies."""
    if COOKIES_FILE.exists():
        with open(COOKIES_FILE) as f:
            cookies = json.load(f)
        context.add_cookies(cookies)
        logger.info("Loaded saved cookies")
        return True
    return False


def take_screenshot(page, name):
    """Take a debug screenshot."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{name}_{timestamp}.png"
    page.screenshot(path=str(path), full_page=True)
    logger.info(f"Screenshot saved: {path}")
    return path


def login(page) -> bool:
    """
    Log into HSBC US online banking.

    HSBC US login flow (as of 2025):
    - URL: https://www.us.hsbc.com/online-banking/
    - The login form is embedded on the page or in an iframe
    - Username field → Continue → Password field → Log on
    - After login, redirects to dashboard at onlinebanking.us.hsbc.com
    """
    login_url = Config.get_login_url()
    logger.info(f"Navigating to HSBC US login: {login_url}")

    page.goto(login_url, wait_until="networkidle", timeout=60000)
    take_screenshot(page, "01_login_page")

    # Check if already authenticated (persistent profile with valid session)
    current_url = page.url.lower()
    if (
        "onlinebanking.us.hsbc.com" in current_url
        or "my-dashboard" in current_url
        or ("dashboard" in current_url and "us.hsbc.com" in current_url)
    ):
        logger.info(f"Already authenticated - session reused from persistent profile: {page.url}")
        return True

    # Also check: if the security frame doesn't appear within 5s, assume already logged in
    security_frame_found = False
    for _ in range(10):  # poll for up to 5s (10 × 0.5s)
        page.wait_for_timeout(500)
        if any("security" in f.url for f in page.frames):
            security_frame_found = True
            break
    if not security_frame_found:
        take_screenshot(page, "01b_no_security_frame")
        logger.info(f"No security/login frame detected — assuming already logged in at: {page.url}")
        return True

    # HSBC US sometimes uses an iframe for the login form (possibly cross-origin)
    # Try main page first, then all frames
    username_selectors = [
        'input[name="userid"]',
        'input[id="userid"]',
        'input[name="u_UserID"]',
        'input[id="u_UserID"]',
        'input[name="username"]',
        'input[id="username"]',
        'input[autocomplete="username"]',
        'input[placeholder*="Username" i]',
        'input[placeholder*="User name" i]',
        # HSBC US specific: the input inside the logon component
        '#logonComponent input[type="text"]',
        'form[name="logonForm"] input[type="text"]',
        # Generic fallback
        'input[type="text"]:visible',
        'input[type="text"]',
    ]

    # --- STEP 1: Enter Username ---
    # Search for username field in the main page and ALL frames
    username_field = None
    login_frame = page

    # Build list: main page first, then all frames
    frames_to_try = [page] + list(page.frames)
    for frame in frames_to_try:
        logger.info(f"Checking frame: {frame.url}")
        for selector in username_selectors:
            try:
                field = frame.wait_for_selector(selector, timeout=2000)
                if field and field.is_visible():
                    username_field = field
                    login_frame = frame
                    logger.info(f"Found username field in frame {frame.url}: {selector}")
                    break
            except Exception:
                continue
        if username_field:
            break

    if not username_field:
        take_screenshot(page, "error_no_username_field")
        logger.error(
            "Could not find username field. "
            "Check screenshots/error_no_username_field_*.png"
        )
        return False

    username_field.fill(Config.HSBC_USERNAME)
    logger.info("Username entered")

    # --- STEP 2: Click Continue (HSBC US has a 2-step login) ---
    continue_selectors = [
        'button:has-text("Continue")',
        'input[value="Continue"]',
        'button[type="submit"]:has-text("Continue")',
        '#continueButton',
        'a:has-text("Continue")',
        # If it's a single-page login, skip to password
    ]

    clicked_continue = False
    for selector in continue_selectors:
        try:
            btn = login_frame.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                clicked_continue = True
                logger.info(f"Clicked Continue: {selector}")
                take_screenshot(page, "02_after_continue")
                break
        except Exception:
            continue

    if not clicked_continue:
        logger.info("No Continue button found - may be single-page login")

    # --- STEP 2b: Click "Log on using password" if security device screen appears ---
    # HSBC defaults to Digital Security Device after Continue; wait for the page to settle
    # then look for the "Log on using password" link before trying the password field.
    page.wait_for_timeout(3000)
    take_screenshot(page, "02a_waiting_for_security_or_password")

    password_link_selectors = [
        'a:has-text("Log on using password")',
        'a:has-text("log on using password")',
        'button:has-text("Log on using password")',
        'a:has-text("Log on using password")',  # case-insensitive handled by has-text
        'a:has-text("Use password instead")',
        'a:has-text("Use my password")',
        'a[href*="password"]:visible',
    ]

    password_link_found = False
    frames_to_try_now = [page] + list(page.frames)
    for frame in frames_to_try_now:
        for selector in password_link_selectors:
            try:
                # Use wait_for_selector with 12s timeout to handle delayed page load
                link = frame.wait_for_selector(selector, timeout=12000)
                if link and link.is_visible():
                    logger.info(f"Found 'Log on using password' link: {selector} — clicking it")
                    link.click()
                    page.wait_for_timeout(3000)
                    take_screenshot(page, "02b_after_password_link")
                    password_link_found = True
                    break
            except Exception:
                continue
        if password_link_found:
            break

    if not password_link_found:
        logger.info("No 'Log on using password' link found — assuming password field is already visible")

    # --- STEP 3: Enter Password ---
    # Re-detect frame after possible navigation - check ALL frames
    password_selectors = [
        'input[name="password"]',
        'input[id="password"]',
        'input[type="password"]',
        'input[name="memorableAnswer"]',
        'input[autocomplete="current-password"]',
        '#logonComponent input[type="password"]',
        'form[name="logonForm"] input[type="password"]',
    ]

    password_field = None
    login_frame = page
    frames_to_try = [page] + list(page.frames)
    for frame in frames_to_try:
        for selector in password_selectors:
            try:
                field = frame.wait_for_selector(selector, timeout=3000)
                if field and field.is_visible():
                    password_field = field
                    login_frame = frame
                    logger.info(f"Found password field in frame {frame.url}: {selector}")
                    break
            except Exception:
                continue
        if password_field:
            break

    if not password_field:
        take_screenshot(page, "error_no_password_field")
        logger.error(
            "Could not find password field. "
            "Check screenshots/error_no_password_field_*.png"
        )
        return False

    password_field.fill(Config.HSBC_PASSWORD)
    logger.info("Password entered")

    # --- STEP 4: Click Log on ---
    logon_selectors = [
        'button:has-text("Log on")',
        'button:has-text("Logon")',
        'button:has-text("Log On")',
        'input[value="Log on"]',
        'input[value="Logon"]',
        'button[type="submit"]',
        '#logonButton',
        'button:has-text("Sign in")',
        'button:has-text("Login")',
    ]

    for selector in logon_selectors:
        try:
            btn = login_frame.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                logger.info(f"Clicked Log on: {selector}")
                break
        except Exception:
            continue

    # Wait for post-login navigation
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass

    # Wait for post-login / any 2FA page to load and settle
    page.wait_for_timeout(10000)
    take_screenshot(page, "03_after_login")

    # --- Detect "We don't recognize your browser" device verification challenge ---
    # HSBC shows this when the persistent profile's device cookie has expired or
    # the browser fingerprint changed. It requires human interaction to resolve.
    device_challenge_selectors = [
        'text="We don\'t recognize your browser"',
        'text="don\'t recognize your browser"',
        'text="Generate a code using the Mobile Banking App"',
        'text="Send SMS"',
        'text="Unable to generate or receive code"',
    ]
    on_device_challenge = False
    for sel in device_challenge_selectors:
        try:
            if page.query_selector(sel):
                on_device_challenge = True
                break
        except Exception:
            continue

    if on_device_challenge:
        take_screenshot(page, "03b_device_challenge")
        if not Config.HEADLESS:
            logger.warning(
                "HSBC device verification challenge detected. "
                "Please complete the verification in the browser window "
                "(choose SMS, email, or app code). Waiting up to 3 minutes..."
            )
            for _ in range(360):  # 3 min at 0.5s intervals
                page.wait_for_timeout(500)
                url = page.url.lower()
                if "onlinebanking.us.hsbc.com" in url or (
                    "dashboard" in url and "us.hsbc.com" in url
                ):
                    logger.info(f"Device verification completed — redirected to: {page.url}")
                    take_screenshot(page, "03c_after_device_verify")
                    return True
            logger.error("Timed out waiting for device verification (3 min elapsed)")
            take_screenshot(page, "error_device_verify_timeout")
            return False
        else:
            raise RuntimeError(
                "HSBC device verification required but browser is running headless. "
                "Run once with HEADLESS=false to register this browser:\n"
                "  HEADLESS=false python -m src.main --dry-run\n"
                "Complete the verification in the browser window, then switch back to headless."
            )

    # Check for successful login indicators (HSBC US dashboard)
    success_indicators = [
        'text="My Dashboard"',
        'text="Account Summary"',
        'text="My accounts"',
        'text="Welcome"',
        'text="Your accounts"',
        '[class*="dashboard"]',
        '[class*="account-summary"]',
        # URL-based check
    ]

    # Check URL - successful login redirects to onlinebanking.us.hsbc.com
    current_url = page.url.lower()
    if "onlinebanking.us.hsbc.com" in current_url or "dashboard" in current_url:
        logger.info(f"Login successful - redirected to: {page.url}")
        return True

    for indicator in success_indicators:
        try:
            if page.query_selector(indicator):
                logger.info("Login successful - found dashboard element")
                return True
        except Exception:
            continue

    # Check for error messages
    error_indicators = [
        'text="incorrect"',
        'text="invalid"',
        'text="not recognized"',
        'text="locked"',
        '[class*="error-message"]',
        '[class*="alert-danger"]',
    ]

    for indicator in error_indicators:
        try:
            el = page.query_selector(indicator)
            if el and el.is_visible():
                error_text = el.text_content()
                logger.error(f"Login failed: {error_text}")
                take_screenshot(page, "error_login_failed")
                return False
        except Exception:
            continue

    logger.warning("Could not confirm login status, proceeding anyway")
    return True


def _save_page_html(page, name: str):
    """Save current page HTML for debugging."""
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCREENSHOTS_DIR / f"debug_{name}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.content())
    logger.info(f"Saved page HTML to {path}")


def _has_transaction_content(page) -> bool:
    """Check if the current page has transaction table content."""
    for selector in ["#transaction-table", "#transactions", ".transaction", "[class*='transaction']"]:
        try:
            el = page.query_selector(selector)
            if el:
                logger.info(f"Found transaction content: {selector}")
                return True
        except Exception:
            continue
    return False


def _do_sso_and_wait(page, sso_url: str) -> bool:
    """Navigate to the SSO URL and wait for redirect to rewards.us.hsbc.com. Returns True on success."""
    try:
        page.goto(sso_url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        logger.warning(f"SSO navigation error (proceeding): {e}")
    for _ in range(40):  # up to 20s
        if "rewards.us.hsbc.com" in page.url:
            return True
        page.wait_for_timeout(500)
    return "rewards.us.hsbc.com" in page.url


def navigate_to_rewards(page) -> bool:
    """
    Navigate to the HSBC US rewards portal and find the transaction activity page.

    Flow:
    1. Navigate to the LaunchOver SSO URL → lands on rewards.us.hsbc.com
    2. If SSO fails (redirected to /security or stays on us.hsbc.com), re-login and retry
    3. Navigate to /account/transactions/ and wait for the date-range select to appear
    """
    take_screenshot(page, "04_pre_rewards_navigation")

    sso_url = "https://www.lgsso.online-banking.us.hsbc.com/lgapp-rwdeng/services/LaunchOver"
    transactions_url = "https://rewards.us.hsbc.com/account/transactions/"

    # --- Step 1: Navigate via SSO to rewards portal ---
    logger.info(f"Navigating to rewards SSO URL: {sso_url}")
    sso_ok = _do_sso_and_wait(page, sso_url)
    logger.info(f"SSO result — at: {page.url}")

    # --- Step 2: Handle SSO failure (expired token → /security or stayed on us.hsbc.com) ---
    if not sso_ok or "/security" in page.url or "rewards.us.hsbc.com" not in page.url:
        logger.warning(f"SSO redirect failed (landed on {page.url}), attempting re-login...")
        if not login(page):
            raise RuntimeError("Re-login after SSO failure also failed")
        logger.info("Re-navigating to SSO URL after re-login...")
        sso_ok = _do_sso_and_wait(page, sso_url)
        if not sso_ok:
            take_screenshot(page, "error_sso_retry_failed")
            raise RuntimeError(f"SSO redirect failed after re-login — still on: {page.url}")
        logger.info(f"SSO retry succeeded — at: {page.url}")

    # Give the Nuxt SPA a moment to establish auth state
    page.wait_for_timeout(3000)
    take_screenshot(page, "05a_rewards_landing")

    # --- Step 3: Navigate to transactions page and wait for SPA to render ---
    logger.info(f"Navigating to transactions page: {transactions_url}")
    try:
        page.goto(transactions_url, wait_until="networkidle", timeout=20000)
    except Exception as e:
        logger.warning(f"Transactions page navigation error (proceeding): {e}")

    # Wait for the date-range select to confirm the SPA has loaded the transactions view.
    # If it doesn't appear, retry once after a short wait.
    date_select = None
    for attempt in range(2):
        try:
            date_select = page.wait_for_selector("select.custom-select", timeout=15000)
        except Exception:
            date_select = None
        if date_select:
            break
        if attempt == 0:
            logger.warning(
                f"Date-range select not found on attempt 1 (URL: {page.url}), retrying..."
            )
            page.wait_for_timeout(3000)
            try:
                page.goto(transactions_url, wait_until="networkidle", timeout=20000)
            except Exception as e:
                logger.warning(f"Transactions page retry navigation error: {e}")

    if not date_select:
        logger.warning(f"Date-range select still not found after retry (URL: {page.url})")

    take_screenshot(page, "05_rewards_page")
    _save_page_html(page, "rewards_transactions")
    logger.info(f"Transactions page URL: {page.url}")
    return True


def scrape_transactions_with_points(page) -> list[Transaction]:
    """
    Scrape HSBC US credit card transactions with their points.

    HSBC US typically shows rewards activity in a table format:
    - Date | Description | Amount | Points Earned

    The page may have multiple sections:
    - Current statement period
    - Previous statement periods
    - Pending transactions (no points yet)
    """
    transactions = []
    now = datetime.now().isoformat()

    # Wait for dynamic content to load
    page.wait_for_timeout(3000)
    take_screenshot(page, "06_scraping_start")

    # The HSBC rewards portal has a <select class="custom-select"> date-range filter.
    # Options (by value): 7, 15, 30, 180 (6 months = widest recent), old (pre-cutoff).
    # Scrape each period that yields rows and combine results.
    def _scrape_table_rows(seen_descs: set) -> list[Transaction]:
        """Extract transaction rows from #transaction-table on the current page."""
        result = []
        try:
            table = page.query_selector("#transaction-table")
            if not table:
                return result
            rows = table.query_selector_all("tr")
            for row in rows[1:]:  # skip header
                cells = row.query_selector_all("td")
                if len(cells) < 3:
                    continue
                raw_desc = (cells[0].text_content() or "").strip()
                if not raw_desc:
                    continue
                raw_date = ""

                # Handle redemption rows ("Pay with your Points! Redeem X Points ($Y.YY)")
                if "Pay with your Points" in raw_desc or "Redeem" in raw_desc:
                    raw_desc = "[Redemption] " + raw_desc.split("Redeem")[0].strip()
                    # No date in these rows; leave raw_date empty
                else:
                    # Pattern covers: Purchase, Purchase Return, Fee, Other, etc.
                    m = re.match(
                        r'^(\w+ \d+, \d{4}) - (?:Purchase Return|Purchase|Fee|Other)?(.*)$',
                        raw_desc,
                    )
                    if m:
                        raw_date = m.group(1).strip()
                        raw_desc = m.group(2).strip()
                amount = (cells[1].text_content() or "").strip() if len(cells) > 1 else ""
                points = (cells[4].text_content() or "").strip() if len(cells) > 4 else ""
                dedup_key = f"{raw_date}|{raw_desc}|{amount}"
                if dedup_key in seen_descs:
                    continue
                seen_descs.add(dedup_key)
                result.append(Transaction(
                    date=raw_date,
                    description=raw_desc,
                    amount=amount,
                    currency="USD",
                    points=points,
                    card_last_four="",
                    scraped_at=now,
                ))
        except Exception as e:
            logger.debug(f"Table row extraction error: {e}")
        return result

    seen_keys: set = set()
    date_filter = page.query_selector("select.custom-select")
    if date_filter:
        # The HSBC rewards SPA has a quirk: selecting value='old' directly returns 0 rows.
        # Selecting value='180' first (which empties the table — a SPA bug), then
        # selecting value='old', causes the SPA to render ALL available transactions.
        logger.info("Selecting period '180' (priming SPA state)...")
        date_filter.select_option(value="180")
        page.wait_for_timeout(3000)

        logger.info("Selecting period 'old' (all available transactions)...")
        date_filter = page.query_selector("select.custom-select")
        date_filter.select_option(value="old")
        page.wait_for_timeout(3000)

        transactions.extend(_scrape_table_rows(seen_keys))
        logger.info(f"Scraped {len(transactions)} transactions after period select")
        take_screenshot(page, "06b_all_periods_scraped")
    else:
        logger.info("No date-range select found — scraping current view")

    # --- Strategy 1: HTML tables ---
    table_selectors = [
        "table",  # Try all tables on the page
        'table[class*="reward"]',
        'table[class*="transaction"]',
        'table[class*="activity"]',
        'table[class*="points"]',
        'table[class*="statement"]',
        '[role="table"]',
        '[class*="data-table"]',
    ]

    for table_sel in table_selectors:
        try:
            tables = page.query_selector_all(table_sel)
            for table in tables:
                rows = table.query_selector_all("tr")
                if len(rows) < 2:
                    continue

                # Detect header to understand column layout
                header_row = rows[0]
                headers = [
                    th.text_content().strip().lower()
                    for th in header_row.query_selector_all("th, td")
                ]
                logger.info(f"Table headers: {headers}")

                # Find column indices
                date_idx = _find_column(headers, ["date", "trans date", "transaction date"])
                desc_idx = _find_column(headers, ["description", "merchant", "details", "transaction"])
                amount_idx = _find_column(headers, ["amount", "value", "charge"])
                points_idx = _find_column(headers, ["total", "points", "reward", "earned", "bonus"])

                if points_idx == -1:
                    logger.debug(f"No points column found in headers: {headers}")
                    continue

                logger.info(
                    f"Column mapping - date:{date_idx} desc:{desc_idx} "
                    f"amount:{amount_idx} points:{points_idx}"
                )

                for row in rows[1:]:
                    cells = row.query_selector_all("td")
                    if len(cells) <= max(
                        i for i in [date_idx, desc_idx, amount_idx, points_idx] if i >= 0
                    ):
                        continue

                    raw_desc = _cell_text(cells, desc_idx)
                    raw_date = _cell_text(cells, date_idx)

                    # Parse date embedded in description:
                    # e.g. "Mar 20, 2026 - PurchaseANTHROPIC" → date + clean desc
                    date_in_desc = re.match(
                        r'^(\w+ \d+, \d{4}) - (?:Purchase Return|Purchase|Fee|Other)?(.*)$', raw_desc
                    )
                    if date_in_desc:
                        raw_date = date_in_desc.group(1)
                        raw_desc = date_in_desc.group(2).strip()

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
                        scraped_at=now,
                    )
                    if txn.description and txn.description != "N/A":
                        transactions.append(txn)

                if transactions:
                    logger.info(
                        f"Extracted {len(transactions)} transactions from table"
                    )
                    break
        except Exception as e:
            logger.debug(f"Table strategy error for {table_sel}: {e}")
            continue

        if transactions:
            break

    # --- Strategy 2: Styled list/card layout ---
    if not transactions:
        item_selectors = [
            '[class*="transaction-row"]',
            '[class*="activity-row"]',
            '[class*="reward-row"]',
            '[class*="transaction-item"]',
            '[class*="activity-item"]',
            'li[class*="transaction"]',
            'li[class*="activity"]',
            'div[class*="txn-row"]',
            '[class*="statement-line"]',
            '[data-testid*="transaction"]',
            '[data-testid*="activity"]',
        ]

        for item_sel in item_selectors:
            try:
                items = page.query_selector_all(item_sel)
                if not items:
                    continue

                logger.info(f"Found {len(items)} items with: {item_sel}")

                for item in items:
                    date_el = item.query_selector(
                        '[class*="date"], time, [data-field="date"]'
                    )
                    desc_el = item.query_selector(
                        '[class*="desc"], [class*="merchant"], '
                        '[class*="name"], [data-field="description"]'
                    )
                    amount_el = item.query_selector(
                        '[class*="amount"], [class*="value"], [data-field="amount"]'
                    )
                    points_el = item.query_selector(
                        '[class*="point"], [class*="reward"], '
                        '[class*="earn"], [data-field="points"]'
                    )

                    txn = Transaction(
                        date=date_el.text_content().strip() if date_el else "",
                        description=(
                            desc_el.text_content().strip()
                            if desc_el
                            else item.text_content().strip()[:100]
                        ),
                        amount=amount_el.text_content().strip() if amount_el else "",
                        currency="USD",
                        points=points_el.text_content().strip() if points_el else "N/A",
                        card_last_four="",
                        scraped_at=now,
                    )
                    if txn.description:
                        transactions.append(txn)

                if transactions:
                    break
            except Exception as e:
                logger.debug(f"Item strategy error for {item_sel}: {e}")
                continue

    # --- Pagination: click "Load more" / "Next" to get all transactions ---
    if transactions:
        page_num = 1
        while True:
            load_more = None
            for sel in [
                'button:has-text("Load more")',
                'button:has-text("Show more")',
                'a:has-text("Load more")',
                'a:has-text("Next")',
                'button:has-text("Next")',
                '[class*="load-more"]',
                '[class*="pagination"] [aria-label="Next"]',
            ]:
                try:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        load_more = el
                        break
                except Exception:
                    continue

            if not load_more:
                break

            page_num += 1
            logger.info(f"Loading more transactions (page {page_num})...")
            load_more.click()
            page.wait_for_timeout(3000)

            # Scrape newly loaded rows from the same table
            new_count = 0
            try:
                tables = page.query_selector_all("#transaction-table, table")
                for table in tables:
                    rows = table.query_selector_all("tr")
                    for row in rows[1 + len(transactions):]:  # skip already-scraped rows
                        cells = row.query_selector_all("td")
                        if len(cells) < 2:
                            continue
                        raw_desc = cells[0].text_content().strip() if cells else ""
                        raw_date = ""
                        m = re.match(r'^(\w+ \d+, \d{4}) - (?:Purchase)?(.*)$', raw_desc)
                        if m:
                            raw_date = m.group(1)
                            raw_desc = m.group(2).strip()
                        points_text = cells[4].text_content().strip() if len(cells) > 4 else ""
                        amount_text = cells[1].text_content().strip() if len(cells) > 1 else ""
                        if raw_desc:
                            transactions.append(Transaction(
                                date=raw_date,
                                description=raw_desc,
                                amount=amount_text,
                                currency="USD",
                                points=points_text,
                                card_last_four="",
                                scraped_at=now,
                            ))
                            new_count += 1
            except Exception as e:
                logger.debug(f"Pagination scrape error: {e}")

            if new_count == 0:
                logger.info("No new rows after pagination — stopping")
                break
            logger.info(f"Loaded {new_count} more transactions (total: {len(transactions)})")

    # --- Strategy 3: Parse visible page text as a fallback ---
    if not transactions:
        logger.warning(
            "Structured scraping failed. Saving page content for manual inspection."
        )
        take_screenshot(page, "07_fallback_full_page")

        html_content = page.content()
        debug_path = SCREENSHOTS_DIR / "page_debug.html"
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(
            f"Saved page HTML to {debug_path}. "
            "Open this file in a browser to inspect the structure "
            "and update scrape_transactions_with_points() selectors."
        )

        # Try to extract any tabular text patterns
        body_text = page.text_content("body") or ""
        # Look for date patterns followed by text and numbers
        # Pattern: MM/DD/YYYY or MM/DD description $amount points
        date_pattern = re.compile(
            r"(\d{1,2}/\d{1,2}/\d{2,4})\s+"  # date
            r"(.+?)\s+"  # description
            r"\$?([\d,]+\.?\d*)\s+"  # amount
            r"([\d,]+)\s*(?:pts?|points?)",  # points
            re.IGNORECASE,
        )
        matches = date_pattern.findall(body_text)
        for match in matches:
            txn = Transaction(
                date=match[0],
                description=match[1].strip(),
                amount=f"${match[2]}",
                currency="USD",
                points=match[3],
                card_last_four="",
                scraped_at=now,
            )
            transactions.append(txn)

        if transactions:
            logger.info(
                f"Extracted {len(transactions)} transactions via text pattern matching"
            )

    logger.info(f"Total scraped: {len(transactions)} transactions with points")
    return transactions


def _find_column(headers: list[str], keywords: list[str]) -> int:
    """Find column index matching any of the keywords, in keyword priority order."""
    for kw in keywords:
        for i, h in enumerate(headers):
            if kw in h:
                return i
    return -1


def _cell_text(cells, idx: int) -> str:
    """Get text from cell at index, or 'N/A' if invalid."""
    if idx < 0 or idx >= len(cells):
        return "N/A"
    return cells[idx].text_content().strip()


BROWSER_PROFILE_DIR = Path("credentials/browser_profile")


def scrape_hsbc_points() -> list[dict]:
    """
    Main entry point. Launches browser, logs in, scrapes points, returns data.

    Uses a persistent browser profile so HSBC remembers the device between runs,
    avoiding repeated device-verification challenges after the first manual login.
    """
    transactions = []
    BROWSER_PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"Using persistent browser profile at {BROWSER_PROFILE_DIR}")

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_PROFILE_DIR),
            headless=Config.HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )

        page = context.new_page()

        try:
            if not login(page):
                logger.error("Login failed")
                context.close()
                return []

            if not navigate_to_rewards(page):
                logger.error("Could not navigate to rewards page")
                context.close()
                return []

            raw_transactions = scrape_transactions_with_points(page)
            transactions = [asdict(t) for t in raw_transactions]

        except Exception as e:
            logger.error(f"Scraping error: {e}")
            take_screenshot(page, "error_exception")
            raise
        finally:
            context.close()

    return transactions
