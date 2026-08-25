"""Config from the environment, plus .env if there is one.

Values are read lazily rather than captured at import, so import ordering
doesn't matter and tests can override credentials without reimporting.
"""

import os

from dotenv import load_dotenv

load_dotenv()  # no-op when there is no .env file


def razorpay_credentials() -> tuple[str | None, str | None]:
    return os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")


def razorpay_live_mode() -> bool:
    """True when real Razorpay test-mode credentials are configured.

    'Live mode' here means 'really calls Razorpay' — the credentials are still
    test-mode keys (rzp_test_*); this project never touches real money.
    """
    key_id, key_secret = razorpay_credentials()
    return bool(key_id and key_secret)
