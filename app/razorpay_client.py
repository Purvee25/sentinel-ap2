"""Razorpay test-mode order creation.

Falls back to mock ids when no credentials are set, so the project runs
without a Razorpay account. Nothing here decides whether a purchase is
allowed — guardrail.py has already approved it. Failures in this module are
execution failures, not policy ones.
"""

import logging
import uuid

import requests

from app.config import razorpay_credentials, razorpay_live_mode

_TIMEOUT_SECONDS = 10


class _TimeoutSession(requests.Session):
    """requests.Session that enforces a default timeout on every call."""

    def request(self, method, url, **kwargs):  # type: ignore[override]
        kwargs.setdefault("timeout", _TIMEOUT_SECONDS)
        return super().request(method, url, **kwargs)

logger = logging.getLogger(__name__)

_client = None


class PaymentExecutionError(RuntimeError):
    """Raised when Razorpay rejects or fails an order the guardrail approved."""


def mock_mode() -> bool:
    return not razorpay_live_mode()


def _get_client():
    global _client
    if _client is None:
        import razorpay  # imported lazily: not needed at all in mock mode

        key_id, key_secret = razorpay_credentials()
        _client = razorpay.Client(session=_TimeoutSession(), auth=(key_id, key_secret))
        _client.set_app_details({"title": "Sentinel-AP2", "version": "0.1.0"})
    return _client


def create_test_order(amount_paise: int, receipt: str) -> str:
    """Create a Razorpay test-mode order and return its id.

    Returns a `order_mock_*` id when no credentials are configured.
    Raises PaymentExecutionError if a real call fails.
    """
    if mock_mode():
        return f"order_mock_{uuid.uuid4().hex[:14]}"

    client = _get_client()  # ImportError/config problems propagate as-is, not as payment failures

    try:
        order = client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            # Razorpay caps receipt at 40 chars
            "receipt": receipt[:40],
            "payment_capture": 1,
            "notes": {"issued_by": "sentinel-ap2"},
        })
    except Exception as exc:  # razorpay raises a family of errors; treat all as execution failure
        logger.exception("Razorpay order creation failed for receipt %s", receipt)
        raise PaymentExecutionError(str(exc)) from exc

    order_id = order.get("id")
    if not order_id:
        raise PaymentExecutionError(f"Razorpay returned no order id: {order!r}")

    logger.info("Created Razorpay test order %s for %s paise", order_id, amount_paise)
    return order_id
