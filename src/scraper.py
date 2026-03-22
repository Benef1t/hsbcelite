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

    # HSBC US sometimes uses an iframe for the login form
    login_frame = page
    frames = page.frames
    for frame in frames:
        if "login" in frame.url.lower() or "logon" in frame.url.lower():
            login_frame = frame
            logger.info(f"Found login iframe: {frame.url}")
            break

    # --- STEP 1: Enter Username ---
    # HSBC US login field selectors
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
    ]

    username_field = None
    for selector in username_selectors:
        try:
            field = login_frame.wait_for_selector(selector, timeout=3000)
            if field and field.is_visible():
                username_field = field
                logger.info(f"Found username field: {selector}")
                break
        except Exception:
            continue

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

    # --- STEP 3: Enter Password ---
    # Re-detect frame after possible navigation
    login_frame = page
    for frame in page.frames:
        if "login" in frame.url.lower() or "logon" in frame.url.lower():
            login_frame = frame
            break

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
    for selector in password_selectors:
        try:
            field = login_frame.wait_for_selector(selector, timeout=5000)
            if field and field.is_visible():
                password_field = field
                logger.info(f"Found password field: {selector}")
                break
        except Exception:
            continue

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

    # HSBC US may show security questions or additional verification
    # Wait a moment for any interstitial pages
    page.wait_for_timeout(3000)
    take_screenshot(page, "03_after_login")

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


def navigate_to_rewards(page) -> bool:
    """
    Navigate to the HSBC US credit card rewards/points page.

    HSBC US shows points information in several places:
    1. Rewards section in online banking (per-transaction points breakdown)
    2. Credit card statement view
    3. "View rewards activity" link from dashboard

    The rewards activity page shows each transaction with its earned points.
    """
    take_screenshot(page, "04_pre_rewards_navigation")

    # Strategy 1: Direct URL navigation to known HSBC US rewards pages
    rewards_urls = [
        # HSBC US online banking rewards pages
        "https://onlinebanking.us.hsbc.com/gbi/rewards",
        "https://onlinebanking.us.hsbc.com/gbi/rewards/activity",
        "https://onlinebanking.us.hsbc.com/gbi/rewards/points-activity",
        "https://onlinebanking.us.hsbc.com/gbi/credit-cards/rewards",
        # Alternative paths
        "https://www.us.hsbc.com/credit-cards/rewards/",
        "https://www.us.hsbc.com/rewards/",
    ]

    for url in rewards_urls:
        try:
            logger.info(f"Trying rewards URL: {url}")
            response = page.goto(url, wait_until="networkidle", timeout=20000)
            if response and response.status == 200 and "login" not in page.url.lower():
                take_screenshot(page, "05_rewards_page")
                logger.info(f"Navigated to rewards: {url}")
                return True
        except Exception as e:
            logger.debug(f"URL {url} failed: {e}")
            continue

    # Strategy 2: Navigate from dashboard - click through menus
    # Go back to dashboard first
    try:
        page.goto(
            "https://onlinebanking.us.hsbc.com/gbi/dashboard",
            wait_until="networkidle",
            timeout=20000,
        )
    except Exception:
        pass

    nav_selectors = [
        # Credit cards menu/section
        'a:has-text("Credit Cards")',
        'a:has-text("Credit cards")',
        'a:has-text("Cards")',
        # Rewards specific links
        'a:has-text("Rewards")',
        'a:has-text("Points")',
        'a:has-text("View rewards")',
        'a:has-text("Rewards activity")',
        'a:has-text("Points activity")',
        # HSBC US navigation elements
        '[data-menu-item*="credit"] a',
        '[data-menu-item*="reward"] a',
        'nav a[href*="reward"]',
        'nav a[href*="credit"]',
    ]

    for selector in nav_selectors:
        try:
            link = page.query_selector(selector)
            if link and link.is_visible():
                link.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(2000)
                take_screenshot(page, "05_rewards_page")

                # Check if we're on a rewards-like page
                page_text = page.text_content("body") or ""
                if any(
                    kw in page_text.lower()
                    for kw in ["reward", "points", "earned", "bonus"]
                ):
                    logger.info(f"Found rewards content via: {selector}")
                    return True
        except Exception:
            continue

    # Strategy 3: Look for credit card in account list, then find rewards
    try:
        # Click on credit card account
        cc_selectors = [
            '[class*="credit-card"] a',
            'a[href*="credit-card"]',
            'div:has-text("Credit Card") a',
            '[class*="account-tile"]:has-text("Credit") a',
        ]
        for sel in cc_selectors:
            try:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    el.click()
                    page.wait_for_load_state("networkidle", timeout=15000)
                    take_screenshot(page, "05_credit_card_page")

                    # Now look for rewards/points link on the card page
                    for reward_sel in [
                        'a:has-text("Rewards")',
                        'a:has-text("Points")',
                        'a:has-text("View activity")',
                        '[class*="reward"]',
                    ]:
                        try:
                            r = page.query_selector(reward_sel)
                            if r and r.is_visible():
                                r.click()
                                page.wait_for_load_state(
                                    "networkidle", timeout=15000
                                )
                                take_screenshot(page, "05_rewards_page")
                                logger.info("Found rewards via credit card page")
                                return True
                        except Exception:
                            continue
            except Exception:
                continue
    except Exception:
        pass

    take_screenshot(page, "error_no_rewards_page")
    logger.error(
        "Could not navigate to rewards page. "
        "Check screenshots/ and update navigate_to_rewards() selectors."
    )
    return False


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

    # Check if there are tabs/filters for different periods
    period_selectors = [
        'select[class*="period"]',
        'select[class*="statement"]',
        'button:has-text("Current")',
        'button:has-text("All")',
        '[class*="date-range"]',
        '[class*="filter"]',
    ]
    for sel in period_selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                logger.info(f"Found period/filter control: {sel}")
                # If it's a select, try to select "All" or widest range
                if sel.startswith("select"):
                    options = el.query_selector_all("option")
                    for opt in options:
                        text = opt.text_content().lower()
                        if "all" in text or "12" in text or "year" in text:
                            el.select_option(label=opt.text_content())
                            page.wait_for_load_state("networkidle", timeout=10000)
                            page.wait_for_timeout(2000)
                            break
                break
        except Exception:
            continue

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
                points_idx = _find_column(headers, ["points", "reward", "earned", "bonus"])

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

                    txn = Transaction(
                        date=_cell_text(cells, date_idx),
                        description=_cell_text(cells, desc_idx),
                        amount=_cell_text(cells, amount_idx),
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
    """Find column index matching any of the keywords."""
    for i, h in enumerate(headers):
        for kw in keywords:
            if kw in h:
                return i
    return -1


def _cell_text(cells, idx: int) -> str:
    """Get text from cell at index, or 'N/A' if invalid."""
    if idx < 0 or idx >= len(cells):
        return "N/A"
    return cells[idx].text_content().strip()


def scrape_hsbc_points() -> list[dict]:
    """
    Main entry point. Launches browser, logs in, scrapes points, returns data.
    """
    transactions = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=Config.HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context = browser.new_context(
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

        # Try session reuse with saved cookies
        cookies_loaded = load_cookies(context)

        try:
            if cookies_loaded:
                logger.info("Attempting session reuse...")
                page.goto(
                    "https://onlinebanking.us.hsbc.com/gbi/dashboard",
                    wait_until="networkidle",
                    timeout=20000,
                )
                # If redirected to login, session expired
                if "login" in page.url.lower() or "logon" in page.url.lower():
                    logger.info("Session expired, logging in again")
                    cookies_loaded = False

            if not cookies_loaded:
                if not login(page):
                    logger.error("Login failed")
                    browser.close()
                    return []
                save_cookies(context)

            if not navigate_to_rewards(page):
                logger.error("Could not navigate to rewards page")
                browser.close()
                return []

            raw_transactions = scrape_transactions_with_points(page)
            transactions = [asdict(t) for t in raw_transactions]

        except Exception as e:
            logger.error(f"Scraping error: {e}")
            take_screenshot(page, "error_exception")
            raise
        finally:
            browser.close()

    return transactions
