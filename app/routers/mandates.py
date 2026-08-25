from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.db_models import Mandate
from app.mandate import create_signed_mandate
from app.schemas import MandateCreateRequest, MandateResponse

router = APIRouter(prefix="/mandates", tags=["mandates"])


@router.post("", response_model=MandateResponse)
def issue_mandate(req: MandateCreateRequest, db: Session = Depends(get_db)):
    """Simulates the user's device signing a bounded spending authorization."""
    signed = create_signed_mandate(req.merchant_id, req.max_amount_paise, req.ttl_seconds)
    mandate = Mandate(
        id=signed["id"], merchant_id=signed["merchant_id"],
        max_amount_paise=signed["max_amount_paise"], nonce=signed["nonce"],
        issued_at=signed["issued_at"], expires_at=signed["expires_at"],
        signature_b64=signed["signature_b64"], status="active",
    )
    db.add(mandate)
    db.commit()
    db.refresh(mandate)
    return MandateResponse(**{k: getattr(mandate, k) for k in MandateResponse.model_fields})


@router.get("/{mandate_id}", response_model=MandateResponse)
def get_mandate(mandate_id: str, db: Session = Depends(get_db)):
    mandate = db.query(Mandate).filter(Mandate.id == mandate_id).first()
    if mandate is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="mandate not found")
    return MandateResponse(**{k: getattr(mandate, k) for k in MandateResponse.model_fields})
