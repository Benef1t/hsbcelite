import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # HSBC
    HSBC_USERNAME = os.getenv("HSBC_USERNAME", "")
    HSBC_PASSWORD = os.getenv("HSBC_PASSWORD", "")
    HSBC_BASE_URL = os.getenv("HSBC_BASE_URL", "https://www.hsbc.com.hk")

    # Google Sheets
    GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
    GOOGLE_CREDENTIALS_PATH = os.getenv(
        "GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json"
    )

    # Schedule
    CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "720"))

    # Browser
    HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

    # Region-specific URL patterns
    REGION_URLS = {
        "https://www.hsbc.com.hk": {
            "login": "/ways-to-bank/online-banking/login",
            "rewards": "/rewards",
            "statements": "/credit-cards/statements",
        },
        "https://www.hsbc.com.sg": {
            "login": "/ways-to-bank/online-banking/login",
            "rewards": "/rewards",
            "statements": "/credit-cards/statements",
        },
        "https://www.hsbc.co.uk": {
            "login": "/ways-to-bank/online-banking/login",
            "rewards": "/rewards",
            "statements": "/credit-cards/statements",
        },
        "https://www.hsbc.com.my": {
            "login": "/ways-to-bank/online-banking/login",
            "rewards": "/rewards",
            "statements": "/credit-cards/statements",
        },
    }

    @classmethod
    def get_login_url(cls):
        region = cls.REGION_URLS.get(cls.HSBC_BASE_URL, {})
        return cls.HSBC_BASE_URL + region.get(
            "login", "/ways-to-bank/online-banking/login"
        )

    @classmethod
    def validate(cls):
        errors = []
        if not cls.HSBC_USERNAME:
            errors.append("HSBC_USERNAME is not set")
        if not cls.HSBC_PASSWORD:
            errors.append("HSBC_PASSWORD is not set")
        if not cls.GOOGLE_SHEETS_ID:
            errors.append("GOOGLE_SHEETS_ID is not set")
        if not os.path.exists(cls.GOOGLE_CREDENTIALS_PATH):
            errors.append(
                f"Google credentials file not found at {cls.GOOGLE_CREDENTIALS_PATH}"
            )
        return errors
