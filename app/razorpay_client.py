"""Thin wrapper around Razorpay test-mode order creation.

Runs in MOCK mode automatically when RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET
are not set, so the guardrail logic and test suite work without live
credentials. Only ever called after the guardrail engine has already
accepted a purchase — this module never makes an authorization decision.
"""

import os
import uuid

_KEY_ID = os.getenv("RAZORPAY_KEY_ID")
_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET")
MOCK_MODE = not (_KEY_ID and _KEY_SECRET)

_client = None


def _get_client():
    global _client
    if _client is None:
        import razorpay  # imported lazily: not needed at all in mock mode

        _client = razorpay.Client(auth=(_KEY_ID, _KEY_SECRET))
    return _client


def create_test_order(amount_paise: int, receipt: str) -> str:
    """Returns a Razorpay order id (or a mock id if no credentials configured)."""
    if MOCK_MODE:
        return f"order_mock_{uuid.uuid4().hex[:14]}"

    order = _get_client().order.create(
        {"amount": amount_paise, "currency": "INR", "receipt": receipt, "payment_capture": 1}
    )
    return order["id"]
