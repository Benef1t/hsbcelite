"""
HSBC scraper core — browser lifecycle, navigation, and scraping orchestration.

Owns the Playwright context and coordinates auth, navigation, and parsing.
Uses AuthSession.ensure_authenticated() instead of a re-entrant login() call.
"""

import json
import logging
import shutil
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

from playwright.sync_api import sync_playwright

from ..auth import AuthSession
from ..config import Config
from ._parser import parse_transaction_table
from .models import Transaction

logger = logging.getLogger(__name__)

SCREENSHOTS_BASE_DIR = Path("screenshots")
COOKIES_FILE = Path("credentials/cookies.json")
BROWSER_PROFILE_DIR = Path("credentials/browser_profile")

MAX_RUNS_TO_KEEP = 4

# Per-run screenshot directory, set by _init_run_dir()
_current_run_dir: Path | None = None


# ---------------------------------------------------------------------------
# Cookie persistence
# ---------------------------------------------------------------------------

def save_cookies(context) -> None:
    """Save browser cookies for session reuse."""
    COOKIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(COOKIES_FILE, "w") as f:
        json.dump(context.cookies(), f)
    logger.info("Cookies saved for session reuse")


def load_cookies(context) -> bool:
    """Load previously saved cookies. Returns True if loaded."""
    if COOKIES_FILE.exists():
        with open(COOKIES_FILE) as f:
            context.add_cookies(json.load(f))
        logger.info("Loaded saved cookies")
        return True
    return False


# ---------------------------------------------------------------------------
# Debug helpers
# ---------------------------------------------------------------------------

def _init_run_dir() -> None:
    """Create a timestamped sub-directory for this run and prune old ones."""
    global _current_run_dir
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    _current_run_dir = SCREENSHOTS_BASE_DIR / f"run_{timestamp}"
    _current_run_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Screenshots for this run: %s", _current_run_dir)

    # Keep only the most recent MAX_RUNS_TO_KEEP run directories
    if SCREENSHOTS_BASE_DIR.exists():
        run_dirs = sorted(
            [d for d in SCREENSHOTS_BASE_DIR.iterdir()
             if d.is_dir() and d.name.startswith("run_")],
            key=lambda d: d.name,
        )
        for old_dir in run_dirs[:-MAX_RUNS_TO_KEEP]:
            shutil.rmtree(old_dir)
            logger.info("Pruned old screenshot dir: %s", old_dir)


def _ensure_run_dir() -> Path:
    """Return the current run directory, creating it if needed."""
    if _current_run_dir is None:
        _init_run_dir()
    return _current_run_dir


def _take_screenshot(page, name: str) -> None:
    run_dir = _ensure_run_dir()
    path = run_dir / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        logger.info("Screenshot saved: %s", path)
    except Exception as e:
        logger.debug("Screenshot failed: %s", e)


def _save_page_html(page, name: str) -> None:
    run_dir = _ensure_run_dir()
    path = run_dir / f"debug_{name}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(page.content())
    logger.info("Saved page HTML to %s", path)


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------

def _do_sso_and_wait(page, sso_url: str) -> bool:
    """Navigate to SSO URL and wait for redirect to rewards.us.hsbc.com."""
    try:
        page.goto(sso_url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        logger.warning("SSO navigation error (proceeding): %s", e)
    for _ in range(40):  # up to 20s
        if "rewards.us.hsbc.com" in page.url:
            return True
        page.wait_for_timeout(500)
    return "rewards.us.hsbc.com" in page.url


def navigate_to_rewards(page, auth: AuthSession) -> bool:
    """
    Navigate to the HSBC US rewards portal transaction page.

    On SSO failure, calls auth.ensure_authenticated() (idempotent) and retries
    instead of using the old re-entrant login() pattern.
    """
    _take_screenshot(page, "04_pre_rewards_navigation")

    sso_url = "https://www.lgsso.online-banking.us.hsbc.com/lgapp-rwdeng/services/LaunchOver"
    transactions_url = "https://rewards.us.hsbc.com/account/transactions/"

    logger.info("Navigating to rewards SSO URL: %s", sso_url)
    sso_ok = _do_sso_and_wait(page, sso_url)
    logger.info("SSO result — at: %s", page.url)

    if not sso_ok or "/security" in page.url or "rewards.us.hsbc.com" not in page.url:
        logger.warning(
            "SSO redirect failed (landed on %s), re-authenticating...", page.url
        )
        auth.ensure_authenticated(page)
        logger.info("Re-navigating to SSO URL after re-auth...")
        sso_ok = _do_sso_and_wait(page, sso_url)
        if not sso_ok:
            _take_screenshot(page, "error_sso_retry_failed")
            raise RuntimeError(
                f"SSO redirect failed after re-auth — still on: {page.url}"
            )
        logger.info("SSO retry succeeded — at: %s", page.url)

    page.wait_for_timeout(3000)
    _take_screenshot(page, "05a_rewards_landing")

    logger.info("Navigating to transactions page: %s", transactions_url)
    try:
        page.goto(transactions_url, wait_until="networkidle", timeout=20000)
    except Exception as e:
        logger.warning("Transactions page navigation error (proceeding): %s", e)

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
                "Date-range select not found on attempt 1 (URL: %s), retrying...",
                page.url,
            )
            page.wait_for_timeout(3000)
            try:
                page.goto(transactions_url, wait_until="networkidle", timeout=20000)
            except Exception as e:
                logger.warning("Transactions page retry error: %s", e)

    if not date_select:
        logger.warning(
            "Date-range select still not found after retry (URL: %s)", page.url
        )

    _take_screenshot(page, "05_rewards_page")
    _save_page_html(page, "rewards_transactions")
    logger.info("Transactions page URL: %s", page.url)
    return True


# ---------------------------------------------------------------------------
# Scraping orchestration
# ---------------------------------------------------------------------------

def _scrape_transactions(page) -> List[Transaction]:
    """
    Prime the SPA date filter, extract HTML, parse transactions, handle pagination.
    Returns raw Transaction objects.
    """
    now = datetime.now().isoformat()
    page.wait_for_timeout(3000)
    _take_screenshot(page, "06_scraping_start")

    # SPA priming: selecting '180' then 'old' forces the Nuxt SPA to render
    # all available transactions (selecting 'old' directly yields 0 rows — HSBC bug).
    date_filter = page.query_selector("select.custom-select")
    if date_filter:
        logger.info("Selecting period '180' (priming SPA state)...")
        date_filter.select_option(value="180")
        page.wait_for_timeout(3000)
        date_filter = page.query_selector("select.custom-select")
        date_filter.select_option(value="old")
        page.wait_for_timeout(3000)
        logger.info("Period 'old' selected")
    else:
        logger.info("No date-range select found — scraping current view")

    transactions = parse_transaction_table(page.content(), scraped_at=now)
    logger.info("Scraped %d transactions after period select", len(transactions))
    _take_screenshot(page, "06b_all_periods_scraped")

    if not transactions:
        logger.warning(
            "Structured scraping found no transactions. "
            "Saving page HTML to screenshots/debug_page_debug.html for inspection."
        )
        _take_screenshot(page, "07_fallback_full_page")
        _save_page_html(page, "page_debug")
        return transactions

    # Pagination: click "Load more" / "Next" until no new rows appear
    seen_keys = {f"{t.date}|{t.description}|{t.amount}" for t in transactions}
    page_num = 1
    _LOAD_MORE_SELECTORS = [
        'button:has-text("Load more")',
        'button:has-text("Show more")',
        'a:has-text("Load more")',
        'a:has-text("Next")',
        'button:has-text("Next")',
        '[class*="load-more"]',
        '[class*="pagination"] [aria-label="Next"]',
    ]
    while True:
        load_more = None
        for sel in _LOAD_MORE_SELECTORS:
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
        logger.info("Loading more transactions (page %d)...", page_num)
        load_more.click()
        page.wait_for_timeout(3000)

        new_txns = parse_transaction_table(page.content(), scraped_at=now)
        added = 0
        for t in new_txns:
            key = f"{t.date}|{t.description}|{t.amount}"
            if key not in seen_keys:
                seen_keys.add(key)
                transactions.append(t)
                added += 1

        if added == 0:
            logger.info("No new rows after pagination — stopping")
            break
        logger.info(
            "Loaded %d more transactions (total: %d)", added, len(transactions)
        )

    logger.info("Total scraped: %d transactions", len(transactions))
    return transactions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scrape(
    *,
    headless: Optional[bool] = None,
    profile_dir: Optional[Path] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    as_dicts: bool = True,
) -> Union[List[dict], List[Transaction]]:
    """
    Scrape HSBC US credit card transactions with points.

    All parameters are optional — defaults come from Config / environment variables.

    Examples::

        transactions = scrape()
        transactions = scrape(headless=False)
        transactions = scrape(as_dicts=False)   # returns list[Transaction]
    """
    _headless = headless if headless is not None else Config.HEADLESS
    _profile_dir = profile_dir or BROWSER_PROFILE_DIR
    _username = username or Config.HSBC_USERNAME
    _password = password or Config.HSBC_PASSWORD

    _init_run_dir()

    _profile_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Using persistent browser profile at %s", _profile_dir)

    auth = AuthSession(_username, _password, headless=_headless)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(_profile_dir),
            headless=_headless,
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
            auth.ensure_authenticated(page)
            navigate_to_rewards(page, auth)
            raw_transactions = _scrape_transactions(page)
        except Exception as e:
            logger.error("Scraping error: %s", e)
            _take_screenshot(page, "error_exception")
            raise
        finally:
            context.close()

    if as_dicts:
        return [asdict(t) for t in raw_transactions]
    return raw_transactions


def scrape_hsbc_points() -> list[dict]:
    """
    Backward-compatible entry point. Equivalent to scrape(as_dicts=True).

    Uses persistent browser profile so HSBC remembers the device between runs,
    avoiding repeated device-verification challenges after the first manual login.
    """
    return scrape(as_dicts=True)
