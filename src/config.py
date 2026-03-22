import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # HSBC
    HSBC_USERNAME = os.getenv("HSBC_USERNAME", "")
    HSBC_PASSWORD = os.getenv("HSBC_PASSWORD", "")
    HSBC_BASE_URL = os.getenv("HSBC_BASE_URL", "https://www.us.hsbc.com")

    # Google Sheets
    GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID", "")
    GOOGLE_CREDENTIALS_PATH = os.getenv(
        "GOOGLE_CREDENTIALS_PATH", "credentials/service_account.json"
    )

    # Schedule
    CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "720"))

    # Browser
    HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

    # HSBC US specific URLs
    # The online banking portal for HSBC US
    LOGIN_URL = "https://www.us.hsbc.com/online-banking/"
    # After login, the dashboard/account overview
    DASHBOARD_URL = "https://www.us.hsbc.com/my-dashboard/"
    # Credit card section in online banking (PFM = Personal Financial Management)
    CREDIT_CARD_URL = "https://onlinebanking.us.hsbc.com/gbi/credit-cards"
    # Rewards points page
    REWARDS_URL = "https://onlinebanking.us.hsbc.com/gbi/rewards"

    @classmethod
    def get_login_url(cls):
        return cls.LOGIN_URL

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
