"""
HSBC Credit Card Points Scraper using Playwright.

This scraper logs into HSBC online banking, navigates to the credit card
rewards/points page, and extracts transaction-level points data.

IMPORTANT: HSBC's website structure varies by region and changes frequently.
You may need to update the CSS selectors below to match your specific region.
The scraper includes screenshot capabilities to help debug selector issues.
"""

import json
import logging
import os
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
    Log into HSBC online banking.

    NOTE: This is a template implementation. HSBC login pages vary by region.
    You will likely need to inspect your region's login page and update the
    selectors below. Run with HEADLESS=false first to see what's happening.

    Common HSBC login flows:
    1. Enter username -> Continue -> Enter password -> Login
    2. Enter username + password on same page -> Login
    3. Enter username -> Security questions -> Password
    """
    login_url = Config.get_login_url()
    logger.info(f"Navigating to login page: {login_url}")

    page.goto(login_url, wait_until="networkidle", timeout=30000)
    take_screenshot(page, "01_login_page")

    # --- STEP 1: Enter Username ---
    # Try common HSBC username field selectors
    username_selectors = [
        'input[name="userid"]',
        'input[name="username"]',
        'input[id="username"]',
        'input[name="u_UserID"]',
        'input[placeholder*="username" i]',
        'input[placeholder*="user" i]',
        'input[type="text"]',
    ]

    username_field = None
    for selector in username_selectors:
        try:
            field = page.wait_for_selector(selector, timeout=3000)
            if field and field.is_visible():
                username_field = field
                logger.info(f"Found username field with selector: {selector}")
                break
        except Exception:
            continue

    if not username_field:
        take_screenshot(page, "error_no_username_field")
        logger.error(
            "Could not find username field. Check screenshot and update selectors."
        )
        return False

    username_field.fill(Config.HSBC_USERNAME)

    # --- STEP 2: Some regions have a "Continue" button before password ---
    continue_selectors = [
        'button:has-text("Continue")',
        'button:has-text("continue")',
        'input[type="submit"][value*="Continue" i]',
        'button:has-text("Next")',
    ]

    for selector in continue_selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_load_state("networkidle", timeout=10000)
                logger.info("Clicked continue/next button")
                take_screenshot(page, "02_after_continue")
                break
        except Exception:
            continue

    # --- STEP 3: Enter Password ---
    password_selectors = [
        'input[name="password"]',
        'input[type="password"]',
        'input[name="memorableAnswer"]',
        'input[id="password"]',
    ]

    password_field = None
    for selector in password_selectors:
        try:
            field = page.wait_for_selector(selector, timeout=5000)
            if field and field.is_visible():
                password_field = field
                logger.info(f"Found password field with selector: {selector}")
                break
        except Exception:
            continue

    if not password_field:
        take_screenshot(page, "error_no_password_field")
        logger.error(
            "Could not find password field. Check screenshot and update selectors."
        )
        return False

    password_field.fill(Config.HSBC_PASSWORD)

    # --- STEP 4: Click Login/Submit ---
    login_selectors = [
        'button[type="submit"]',
        'button:has-text("Log on")',
        'button:has-text("Login")',
        'button:has-text("Sign in")',
        'input[type="submit"]',
        'button:has-text("登入")',
        'button:has-text("登录")',
    ]

    for selector in login_selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                logger.info(f"Clicked login button: {selector}")
                break
        except Exception:
            continue

    # Wait for navigation after login
    try:
        page.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass

    take_screenshot(page, "03_after_login")

    # Check if login was successful by looking for common post-login elements
    success_indicators = [
        'text="Account Summary"',
        'text="My banking"',
        'text="账户概览"',
        'text="帳戶概覽"',
        '[class*="account"]',
        '[class*="dashboard"]',
    ]

    for indicator in success_indicators:
        try:
            if page.query_selector(indicator):
                logger.info("Login appears successful")
                return True
        except Exception:
            continue

    # If we can't confirm success, check for error messages
    error_indicators = [
        'text="incorrect"',
        'text="invalid"',
        'text="error"',
        'text="failed"',
        '[class*="error"]',
    ]

    for indicator in error_indicators:
        try:
            el = page.query_selector(indicator)
            if el and el.is_visible():
                logger.error(f"Login failed - error detected: {el.text_content()}")
                take_screenshot(page, "error_login_failed")
                return False
        except Exception:
            continue

    # Assume success if no errors detected
    logger.warning("Could not confirm login status, proceeding anyway")
    return True


def navigate_to_rewards(page) -> bool:
    """
    Navigate to the credit card rewards/points page.

    HSBC typically shows points in one of these locations:
    1. Rewards Programme page (standalone rewards section)
    2. Credit Card Statement page (points column in transaction list)
    3. RewardCash/Points summary page

    Update the navigation logic below based on your region.
    """
    # Try direct URL navigation first
    rewards_urls = [
        "/rewards",
        "/credit-cards/rewards",
        "/reward-cash",
        "/personal/credit-cards/rewards",
        "/gpib/rewards",
    ]

    for url_path in rewards_urls:
        full_url = Config.HSBC_BASE_URL + url_path
        try:
            response = page.goto(full_url, wait_until="networkidle", timeout=15000)
            if response and response.status == 200:
                take_screenshot(page, "04_rewards_page")
                logger.info(f"Navigated to rewards page: {full_url}")
                return True
        except Exception:
            continue

    # Try clicking navigation links
    nav_selectors = [
        'a:has-text("Rewards")',
        'a:has-text("Points")',
        'a:has-text("RewardCash")',
        'a:has-text("奖赏")',
        'a:has-text("獎賞")',
        'a:has-text("积分")',
        'a:has-text("積分")',
    ]

    for selector in nav_selectors:
        try:
            link = page.query_selector(selector)
            if link and link.is_visible():
                link.click()
                page.wait_for_load_state("networkidle", timeout=15000)
                take_screenshot(page, "04_rewards_page")
                logger.info(f"Clicked rewards link: {selector}")
                return True
        except Exception:
            continue

    take_screenshot(page, "error_no_rewards_page")
    logger.error("Could not navigate to rewards page. Check screenshots and update.")
    return False


def scrape_transactions_with_points(page) -> list[Transaction]:
    """
    Scrape credit card transactions with their associated points.

    This is the most region-specific part. HSBC shows points data differently:

    HK: RewardCash earned per transaction in a table
    UK: Points per transaction in statement view
    SG: Rewards points in transaction history

    The scraper tries multiple strategies to find the data.
    """
    transactions = []
    now = datetime.now().isoformat()

    # Strategy 1: Look for a transaction table with points column
    table_selectors = [
        "table.transaction-table",
        "table.reward-table",
        'table[class*="transaction"]',
        'table[class*="reward"]',
        "table.statement-table",
        'div[class*="transaction-list"]',
        'div[class*="reward-list"]',
    ]

    for table_sel in table_selectors:
        try:
            table = page.query_selector(table_sel)
            if not table:
                continue

            rows = table.query_selector_all("tr")
            logger.info(f"Found table with {len(rows)} rows using {table_sel}")

            for row in rows[1:]:  # Skip header
                cells = row.query_selector_all("td")
                if len(cells) >= 3:
                    txn = Transaction(
                        date=cells[0].text_content().strip(),
                        description=cells[1].text_content().strip(),
                        amount=cells[2].text_content().strip() if len(cells) > 2 else "",
                        currency="",
                        points=cells[-1].text_content().strip(),  # Points usually last column
                        card_last_four="",
                        scraped_at=now,
                    )
                    transactions.append(txn)
        except Exception as e:
            logger.debug(f"Table strategy failed for {table_sel}: {e}")
            continue

    # Strategy 2: Look for individual transaction cards/items
    if not transactions:
        item_selectors = [
            'div[class*="transaction-item"]',
            'li[class*="transaction"]',
            'div[class*="reward-item"]',
            'div[class*="txn-row"]',
            '[class*="statement-row"]',
        ]

        for item_sel in item_selectors:
            try:
                items = page.query_selector_all(item_sel)
                if not items:
                    continue

                logger.info(f"Found {len(items)} transaction items using {item_sel}")

                for item in items:
                    # Try to extract structured data from each item
                    date_el = item.query_selector(
                        '[class*="date"], time, [class*="txn-date"]'
                    )
                    desc_el = item.query_selector(
                        '[class*="desc"], [class*="merchant"], [class*="name"]'
                    )
                    amount_el = item.query_selector(
                        '[class*="amount"], [class*="value"]'
                    )
                    points_el = item.query_selector(
                        '[class*="point"], [class*="reward"], [class*="cash"]'
                    )

                    txn = Transaction(
                        date=date_el.text_content().strip() if date_el else "",
                        description=desc_el.text_content().strip() if desc_el else item.text_content().strip()[:100],
                        amount=amount_el.text_content().strip() if amount_el else "",
                        currency="",
                        points=points_el.text_content().strip() if points_el else "N/A",
                        card_last_four="",
                        scraped_at=now,
                    )
                    if txn.description:
                        transactions.append(txn)
            except Exception as e:
                logger.debug(f"Item strategy failed for {item_sel}: {e}")
                continue

    # Strategy 3: Extract all visible text and try to parse it
    if not transactions:
        logger.warning(
            "Could not find structured transaction data. "
            "Taking full-page screenshot for manual inspection."
        )
        take_screenshot(page, "05_page_content_for_debug")

        # Save page HTML for debugging
        html_content = page.content()
        debug_path = SCREENSHOTS_DIR / "page_debug.html"
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        with open(debug_path, "w", encoding="utf-8") as f:
            f.write(html_content)
        logger.info(f"Saved page HTML to {debug_path} for debugging selectors")

    logger.info(f"Scraped {len(transactions)} transactions")
    return transactions


def scrape_hsbc_points() -> list[dict]:
    """
    Main scraping function. Returns list of transaction dicts with points.
    """
    transactions = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=Config.HEADLESS,
            args=["--disable-blink-features=AutomationControlled"],
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        # Try loading saved cookies for session reuse
        cookies_loaded = load_cookies(context)

        try:
            if cookies_loaded:
                # Try accessing rewards directly with saved session
                logger.info("Attempting session reuse with saved cookies...")
                page.goto(
                    Config.HSBC_BASE_URL + "/rewards",
                    wait_until="networkidle",
                    timeout=15000,
                )

                # Check if we're still logged in
                if "login" in page.url.lower():
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
