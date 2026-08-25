"""The deterministic guardrail engine.

This module makes every money decision. It never reads free-text from an
LLM to decide an amount or an approval — prices come only from the catalog
table, totals are computed here, and every check below is a plain
comparison. An LLM-driven agent can call this engine, but it cannot talk
its way past it: there is no natural-language input this code interprets.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.catalog import get_product
from app.db_models import AuditLog, Mandate, Transaction
from app.mandate import verify_mandate_signature
from app.razorpay_client import PaymentExecutionError, create_test_order


@dataclass
class PurchaseResult:
    status: str  # "accepted" | "rejected"
    reason: str
    computed_total_paise: int = 0
    razorpay_payment_id: str = ""
    transaction_id: str = ""


def _audit(db: Session, event_type: str, payload: dict) -> None:
    db.add(AuditLog(event_type=event_type, payload_json=json.dumps(payload, default=str)))
    db.commit()


def _reject(db: Session, mandate_id: str | None, product_id: str, qty: int, reason: str) -> PurchaseResult:
    txn = Transaction(
        mandate_id=mandate_id, product_id=product_id, qty=qty,
        computed_total_paise=0, status="rejected", reason=reason,
    )
    db.add(txn)
    db.commit()
    _audit(db, "purchase_rejected", {
        "mandate_id": mandate_id, "product_id": product_id, "qty": qty, "reason": reason,
    })
    return PurchaseResult(status="rejected", reason=reason, transaction_id=txn.id)


def process_purchase(
    db: Session,
    mandate_id: str,
    requested_merchant_id: str,
    product_id: str,
    qty: int,
    client_claimed_price_paise: int | None = None,
) -> PurchaseResult:
    mandate = db.query(Mandate).filter(Mandate.id == mandate_id).first()
    if mandate is None:
        return _reject(db, mandate_id, product_id, qty, "mandate not found")

    if mandate.status == "consumed":
        return _reject(db, mandate_id, product_id, qty, "mandate already used (replay rejected)")

    if not verify_mandate_signature(mandate):
        return _reject(db, mandate_id, product_id, qty, "invalid mandate signature (tampered mandate)")

    expires_at = datetime.fromisoformat(mandate.expires_at)
    if datetime.now(timezone.utc) > expires_at:
        mandate.status = "expired"
        db.commit()
        return _reject(db, mandate_id, product_id, qty, "mandate expired")

    if requested_merchant_id != mandate.merchant_id:
        return _reject(db, mandate_id, product_id, qty, "merchant mismatch: outside mandate scope")

    product = get_product(db, product_id)
    if product is None:
        return _reject(db, mandate_id, product_id, qty, "product not found in catalog")

    if qty <= 0:
        return _reject(db, mandate_id, product_id, qty, "invalid quantity")

    # Price and total are ALWAYS computed from catalog truth, never from the
    # agent's claim. A mismatched client-claimed price is treated as a
    # tampering signal and rejected outright rather than silently corrected.
    computed_total = product.price_paise * qty
    if client_claimed_price_paise is not None and client_claimed_price_paise != product.price_paise:
        return _reject(
            db, mandate_id, product_id, qty,
            f"price mismatch: agent claimed {client_claimed_price_paise}, "
            f"catalog says {product.price_paise} (possible tampering)",
        )

    if computed_total > mandate.max_amount_paise:
        return _reject(
            db, mandate_id, product_id, qty,
            f"exceeds mandate cap: total {computed_total} > max {mandate.max_amount_paise}",
        )

    # All checks passed. Consume the mandate before calling out, so a crash
    # mid-payment can't be replayed against the same mandate.
    mandate.status = "consumed"
    db.commit()

    try:
        payment_id = create_test_order(computed_total, receipt=f"mandate_{mandate.id}")
    except PaymentExecutionError as exc:
        # The guardrail approved this, but the payment processor failed. The
        # mandate stays consumed: re-opening it would hand an agent a retry
        # against an authorization the user already spent, and we cannot know
        # from here whether the order was created before the failure. The
        # user re-issues a mandate to try again.
        txn = Transaction(
            mandate_id=mandate.id, product_id=product_id, qty=qty,
            computed_total_paise=computed_total, status="failed",
            reason=f"payment execution failed: {exc}",
        )
        db.add(txn)
        db.commit()
        _audit(db, "payment_failed", {
            "mandate_id": mandate.id, "product_id": product_id, "qty": qty,
            "computed_total_paise": computed_total, "error": str(exc),
        })
        return PurchaseResult(
            status="failed", reason=f"payment execution failed: {exc}",
            computed_total_paise=computed_total, transaction_id=txn.id,
        )

    txn = Transaction(
        mandate_id=mandate.id, product_id=product_id, qty=qty,
        computed_total_paise=computed_total, status="accepted",
        reason="within mandate", razorpay_payment_id=payment_id,
    )
    db.add(txn)
    db.commit()
    _audit(db, "purchase_accepted", {
        "mandate_id": mandate.id, "product_id": product_id, "qty": qty,
        "computed_total_paise": computed_total, "razorpay_payment_id": payment_id,
    })

    return PurchaseResult(
        status="accepted", reason="within mandate", computed_total_paise=computed_total,
        razorpay_payment_id=payment_id, transaction_id=txn.id,
    )
