from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.guardrail import process_purchase
from app.schemas import PurchaseRequest, PurchaseResponse

router = APIRouter(prefix="/purchase", tags=["purchase"])


@router.post("", response_model=PurchaseResponse)
def purchase(req: PurchaseRequest, db: Session = Depends(get_db)):
    """The only endpoint an agent calls to spend money. Everything about
    whether this succeeds is decided deterministically in guardrail.py —
    this route is just plumbing."""
    result = process_purchase(
        db=db,
        mandate_id=req.mandate_id,
        requested_merchant_id=req.merchant_id,
        product_id=req.product_id,
        qty=req.qty,
        client_claimed_price_paise=req.client_claimed_price_paise,
    )
    return PurchaseResponse(
        status=result.status, reason=result.reason,
        computed_total_paise=result.computed_total_paise,
        razorpay_payment_id=result.razorpay_payment_id,
        transaction_id=result.transaction_id,
    )
