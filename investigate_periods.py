"""
Investigation script: explore date-range period options on the HSBC rewards
transactions page and count rows per period.
"""
import sys
sys.path.insert(0, "/Users/bruce/hsbcelite")

from playwright.sync_api import sync_playwright
from src.config import Config
from src.scraper import login, take_screenshot

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir="credentials/browser_profile",
        headless=True,
        args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
    )
    page = context.new_page()

    print("=== Logging in ===")
    login(page)
    print(f"Post-login URL: {page.url}")

    print("\n=== Navigating to rewards SSO ===")
    sso_url = "https://www.lgsso.online-banking.us.hsbc.com/lgapp-rwdeng/services/LaunchOver"
    try:
        page.goto(sso_url, wait_until="networkidle", timeout=30000)
    except Exception as e:
        print(f"SSO nav warning: {e}")

    # Wait until we land on rewards.us.hsbc.com (not still on lgsso)
    print(f"URL after goto: {page.url}")
    for _ in range(40):  # up to 20s
        if "rewards.us.hsbc.com" in page.url:
            break
        page.wait_for_timeout(500)
    print(f"URL after SSO wait: {page.url}")

    # Give Nuxt app time to set auth state
    page.wait_for_timeout(3000)

    # Navigate directly to transactions page
    from urllib.parse import urlparse
    parsed = urlparse(page.url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    txn_url = base_url + "/account/transactions/"
    print(f"Navigating to: {txn_url}")
    try:
        page.goto(txn_url, wait_until="networkidle", timeout=20000)
    except Exception as e:
        print(f"Txn nav warning: {e}")
    page.wait_for_timeout(3000)
    print(f"Transactions URL: {page.url}")

    # Inspect select options via JS
    options = page.evaluate(
        "Array.from(document.querySelectorAll('select.custom-select option'))"
        ".map(o => ({value: o.value, text: o.textContent.trim()}))"
    )
    print(f"\n=== Found {len(options)} period options ===")
    for opt in options:
        print(f"  value={repr(opt['value'])}  text={repr(opt['text'])}")

    if not options:
        print("ERROR: No options found. Check that the page loaded correctly.")
        take_screenshot(page, "investigate_no_options")
        context.close()
        sys.exit(1)

    print("\n=== Scraping rows per period ===")
    all_rows = {}
    for opt in options:
        val = opt["value"]
        label = opt["text"]

        page.select_option("select.custom-select", value=val)
        page.wait_for_timeout(2000)

        rows = page.query_selector_all("#transaction-table tr")
        data_rows = rows[1:]  # skip header row
        row_texts = []
        for row in data_rows:
            txt = (row.text_content() or "").strip().replace("\n", " ")
            # Collapse multiple spaces
            import re
            txt = re.sub(r" {2,}", " ", txt)
            row_texts.append(txt)

        all_rows[val] = row_texts
        print(f"\n  Period '{label}' (value={repr(val)}): {len(data_rows)} rows")
        for t in row_texts:
            print(f"    {t[:120]}")

    print("\n=== Summary ===")
    total_unique = set()
    for val, rows in all_rows.items():
        label = next(o["text"] for o in options if o["value"] == val)
        print(f"  {label}: {len(rows)} rows")
        total_unique.update(rows)
    print(f"  Total unique rows across all periods: {len(total_unique)}")

    context.close()
    print("\nDone.")
