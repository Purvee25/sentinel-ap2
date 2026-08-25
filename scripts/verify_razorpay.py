"""Verify Razorpay test-mode credentials actually work.

    python scripts/verify_razorpay.py

Reports which mode the app is in, and — with credentials configured — creates
one real test-mode order and prints its id so it can be matched against the
Razorpay dashboard. Test mode only: no real money moves.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import razorpay_credentials, razorpay_live_mode  # noqa: E402
from app.razorpay_client import PaymentExecutionError, create_test_order  # noqa: E402


def main() -> None:
    key_id, _ = razorpay_credentials()

    if not razorpay_live_mode():
        print("MOCK MODE — no Razorpay credentials found.")
        print()
        print("The app works fine like this; orders get `order_mock_*` ids.")
        print("To use real test-mode credentials:")
        print("  1. Sign in at https://dashboard.razorpay.com/")
        print("  2. Switch the dashboard to Test Mode")
        print("  3. Settings -> API Keys -> Generate Test Key")
        print("  4. Copy .env.example to .env and paste the key id and secret in")
        sys.exit(1)

    if not key_id.startswith("rzp_test_"):
        print(f"REFUSING TO RUN: key id is '{key_id[:12]}...', which is not a test key.")
        print("This project is test-mode only. Use a key that starts with 'rzp_test_'.")
        sys.exit(2)

    print(f"LIVE TEST MODE — using key {key_id}")
    print("Creating one order for ₹1.00 ...")

    try:
        order_id = create_test_order(amount_paise=100, receipt="sentinel_ap2_verify")
    except PaymentExecutionError as exc:
        print(f"\nFAILED — Razorpay rejected the request: {exc}")
        print("Check the key id and secret are correct, and both come from the same test key pair.")
        sys.exit(3)
    except ImportError as exc:
        print(f"\nFAILED — the razorpay SDK could not be imported: {exc}")
        print("Reinstall dependencies: pip install -r requirements.txt")
        sys.exit(4)

    print(f"\nSUCCESS — created order {order_id}")
    print("Find it under Transactions -> Orders in the Razorpay dashboard (Test Mode).")


if __name__ == "__main__":
    main()
