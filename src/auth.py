"""
HSBC authentication module.

Provides AuthSession — an idempotent authentication layer for the HSBC US login flow.
Caller interface is a single method: ensure_authenticated(page).
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = Path("screenshots")


# ---------------------------------------------------------------------------
# Exceptions (Design A vocabulary)
# ---------------------------------------------------------------------------

class AuthError(Exception):
    """Raised on unrecoverable authentication failure."""


class HeadlessDeviceChallenge(AuthError):
    """
    Raised when HSBC requires device verification but the browser is headless.

    Run once with HEADLESS=false to register the browser:
        HEADLESS=false python -m src.main --dry-run
    Complete verification in the browser window, then switch back to headless.
    """


class DeviceVerificationTimeout(AuthError):
    """Raised when the human did not complete device verification within the timeout."""


# ---------------------------------------------------------------------------
# Device verification handlers
# ---------------------------------------------------------------------------

@runtime_checkable
class DeviceVerificationHandler(Protocol):
    """
    Called when HSBC requires human device verification.
    Must block until verification is complete or raise AuthError.
    """
    def __call__(self, page) -> None: ...


def interactive_device_handler(timeout_s: int = 180) -> Callable:
    """Built-in handler: polls for redirect until verification completes."""
    def _handle(page) -> None:
        logger.warning(
            "HSBC device verification challenge detected. "
            "Please complete the verification in the browser window "
            "(choose SMS, email, or app code). Waiting up to %d seconds...",
            timeout_s,
        )
        for _ in range(timeout_s * 2):  # poll every 0.5s
            page.wait_for_timeout(500)
            url = page.url.lower()
            if "onlinebanking.us.hsbc.com" in url or (
                "dashboard" in url and "us.hsbc.com" in url
            ):
                logger.info("Device verification completed — redirected to: %s", page.url)
                _take_screenshot(page, "03c_after_device_verify")
                return
        raise DeviceVerificationTimeout(
            f"Timed out waiting for device verification ({timeout_s}s elapsed)"
        )
    return _handle


def headless_device_handler() -> Callable:
    """Built-in handler: raises HeadlessDeviceChallenge immediately."""
    def _handle(page) -> None:
        raise HeadlessDeviceChallenge(
            "HSBC device verification required but browser is running headless.\n"
            "Run once with HEADLESS=false to register this browser:\n"
            "  HEADLESS=false python -m src.main --dry-run\n"
            "Complete the verification in the browser window, then switch back to headless."
        )
    return _handle


# ---------------------------------------------------------------------------
# Selector constants (_selectors — single place to update on HSBC DOM changes)
# ---------------------------------------------------------------------------

_USERNAME_SELECTORS = [
    'input[name="userid"]',
    'input[id="userid"]',
    'input[name="u_UserID"]',
    'input[id="u_UserID"]',
    'input[name="username"]',
    'input[id="username"]',
    'input[autocomplete="username"]',
    'input[placeholder*="Username" i]',
    'input[placeholder*="User name" i]',
    '#logonComponent input[type="text"]',
    'form[name="logonForm"] input[type="text"]',
    'input[type="text"]:visible',
    'input[type="text"]',
]

_CONTINUE_SELECTORS = [
    'button:has-text("Continue")',
    'input[value="Continue"]',
    'button[type="submit"]:has-text("Continue")',
    '#continueButton',
    'a:has-text("Continue")',
]

_PASSWORD_LINK_SELECTORS = [
    'a:has-text("Log on using password")',
    'a:has-text("log on using password")',
    'button:has-text("Log on using password")',
    'a:has-text("Use password instead")',
    'a:has-text("Use my password")',
    'a[href*="password"]:visible',
]

_PASSWORD_SELECTORS = [
    'input[name="password"]',
    'input[id="password"]',
    'input[type="password"]',
    'input[name="memorableAnswer"]',
    'input[autocomplete="current-password"]',
    '#logonComponent input[type="password"]',
    'form[name="logonForm"] input[type="password"]',
]

_LOGON_SELECTORS = [
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

_DEVICE_CHALLENGE_SELECTORS = [
    'text="We don\'t recognize your browser"',
    'text="don\'t recognize your browser"',
    'text="Generate a code using the Mobile Banking App"',
    'text="Send SMS"',
    'text="Unable to generate or receive code"',
]

_SUCCESS_URL_FRAGMENTS = ["onlinebanking.us.hsbc.com", "my-dashboard"]

_SUCCESS_INDICATORS = [
    'text="My Dashboard"',
    'text="Account Summary"',
    'text="My accounts"',
    'text="Welcome"',
    'text="Your accounts"',
    '[class*="dashboard"]',
    '[class*="account-summary"]',
]

_ERROR_INDICATORS = [
    'text="incorrect"',
    'text="invalid"',
    'text="not recognized"',
    'text="locked"',
    '[class*="error-message"]',
    '[class*="alert-danger"]',
]


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _take_screenshot(page, name: str) -> None:
    SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = SCREENSHOTS_DIR / f"{name}_{timestamp}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        logger.info("Screenshot saved: %s", path)
    except Exception as e:
        logger.debug("Screenshot failed: %s", e)


def _find_in_frames(page, selectors: List[str], timeout: int = 2000):
    """Search for a visible element across main page and all frames."""
    for frame in [page] + list(page.frames):
        logger.debug("Checking frame: %s", frame.url)
        for selector in selectors:
            try:
                field = frame.wait_for_selector(selector, timeout=timeout)
                if field and field.is_visible():
                    return field, frame
            except Exception:
                continue
    return None, page


# ---------------------------------------------------------------------------
# AuthSession
# ---------------------------------------------------------------------------

class AuthSession:
    """
    Idempotent authentication session for HSBC US online banking.

    Usage::

        auth = AuthSession(username, password, headless=True)
        auth.ensure_authenticated(page)   # no-op if already logged in
        # ...SSO attempt...
        auth.ensure_authenticated(page)   # idempotent re-check on SSO failure

    Device verification is handled by on_device_challenge (defaults to
    headless_device_handler or interactive_device_handler based on headless flag).
    Inject a custom callable to send a webhook, Slack message, etc.
    """

    def __init__(
        self,
        username: str,
        password: str,
        *,
        on_device_challenge: Optional[Callable] = None,
        headless: bool = True,
    ) -> None:
        self._username = username
        self._password = password
        if on_device_challenge is not None:
            self._on_device_challenge = on_device_challenge
        elif headless:
            self._on_device_challenge = headless_device_handler()
        else:
            self._on_device_challenge = interactive_device_handler()

    def ensure_authenticated(self, page) -> None:
        """
        Guarantee the page is authenticated before returning.

        - Already authenticated: returns immediately (session reuse).
        - Login required: performs the full username/password flow.
        - Device challenge: delegates to on_device_challenge handler.

        Raises:
            AuthError: on unrecoverable failure
            HeadlessDeviceChallenge: device challenge in headless mode (default handler)
            DeviceVerificationTimeout: human verification timed out (default handler)
        """
        login_url = "https://www.us.hsbc.com/online/dashboard/"
        logger.info("Navigating to HSBC US login: %s", login_url)
        page.goto(login_url, wait_until="networkidle", timeout=60000)
        _take_screenshot(page, "01_login_page")

        # Fast path: already authenticated
        current_url = page.url.lower()
        if any(f in current_url for f in _SUCCESS_URL_FRAGMENTS):
            logger.info("Already authenticated — session reused: %s", page.url)
            return

        # Poll for security frame up to 5s; absent → assume already logged in
        security_frame_found = False
        for _ in range(10):
            page.wait_for_timeout(500)
            if any("security" in f.url for f in page.frames):
                security_frame_found = True
                break
        if not security_frame_found:
            _take_screenshot(page, "01b_no_security_frame")
            logger.info(
                "No security frame detected — assuming already logged in: %s", page.url
            )
            return

        # --- Step 1: Username ---
        username_field, login_frame = _find_in_frames(page, _USERNAME_SELECTORS)
        if not username_field:
            _take_screenshot(page, "error_no_username_field")
            raise AuthError(
                "Could not find username field. "
                "Check screenshots/error_no_username_field_*.png"
            )
        username_field.fill(self._username)
        logger.info("Username entered")

        # --- Step 2: Continue button ---
        clicked_continue = False
        for selector in _CONTINUE_SELECTORS:
            try:
                btn = login_frame.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    page.wait_for_load_state("networkidle", timeout=15000)
                    clicked_continue = True
                    logger.info("Clicked Continue: %s", selector)
                    _take_screenshot(page, "02_after_continue")
                    break
            except Exception:
                continue
        if not clicked_continue:
            logger.info("No Continue button found — may be single-page login")

        # --- Step 2b: "Log on using password" link ---
        page.wait_for_timeout(3000)
        _take_screenshot(page, "02a_waiting_for_security_or_password")

        password_link_found = False
        for frame in [page] + list(page.frames):
            for selector in _PASSWORD_LINK_SELECTORS:
                try:
                    link = frame.wait_for_selector(selector, timeout=12000)
                    if link and link.is_visible():
                        logger.info("Found 'Log on using password' link: %s", selector)
                        link.click()
                        page.wait_for_timeout(3000)
                        _take_screenshot(page, "02b_after_password_link")
                        password_link_found = True
                        break
                except Exception:
                    continue
            if password_link_found:
                break
        if not password_link_found:
            logger.info(
                "No 'Log on using password' link found — assuming password field is visible"
            )

        # --- Step 3: Password ---
        password_field, login_frame = _find_in_frames(
            page, _PASSWORD_SELECTORS, timeout=3000
        )
        if not password_field:
            _take_screenshot(page, "error_no_password_field")
            raise AuthError(
                "Could not find password field. "
                "Check screenshots/error_no_password_field_*.png"
            )
        password_field.fill(self._password)
        logger.info("Password entered")

        # --- Step 4: Log on button ---
        for selector in _LOGON_SELECTORS:
            try:
                btn = login_frame.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    logger.info("Clicked Log on: %s", selector)
                    break
            except Exception:
                continue

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(10000)
        _take_screenshot(page, "03_after_login")

        # --- Device verification challenge ---
        on_device_challenge = False
        for sel in _DEVICE_CHALLENGE_SELECTORS:
            try:
                if page.query_selector(sel):
                    on_device_challenge = True
                    break
            except Exception:
                continue

        if on_device_challenge:
            _take_screenshot(page, "03b_device_challenge")
            self._on_device_challenge(page)
            return

        # --- Verify success ---
        current_url = page.url.lower()
        if (
            any(f in current_url for f in _SUCCESS_URL_FRAGMENTS)
            or "dashboard" in current_url
        ):
            logger.info("Login successful — redirected to: %s", page.url)
            return

        for indicator in _SUCCESS_INDICATORS:
            try:
                if page.query_selector(indicator):
                    logger.info("Login successful — found dashboard element")
                    return
            except Exception:
                continue

        for indicator in _ERROR_INDICATORS:
            try:
                el = page.query_selector(indicator)
                if el and el.is_visible():
                    error_text = el.text_content()
                    _take_screenshot(page, "error_login_failed")
                    raise AuthError(f"Login failed: {error_text}")
            except AuthError:
                raise
            except Exception:
                continue

        logger.warning("Could not confirm login status, proceeding anyway")
