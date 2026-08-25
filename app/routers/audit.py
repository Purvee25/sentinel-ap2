from fastapi import APIRouter, Depends
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.database import get_db
from app.db_models import AuditLog, Product, Transaction
from app.schemas import AuditEntry, ProductResponse

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=list[AuditEntry])
def list_audit_log(db: Session = Depends(get_db), limit: int = 100):
    rows = db.query(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit).all()
    return [
        AuditEntry(id=r.id, event_type=r.event_type, payload_json=r.payload_json,
                   created_at=r.created_at.isoformat())
        for r in rows
    ]


@router.get("/transactions")
def list_transactions(db: Session = Depends(get_db), limit: int = 100):
    rows = db.query(Transaction).order_by(desc(Transaction.created_at)).limit(limit).all()
    return [
        {
            "id": t.id, "mandate_id": t.mandate_id, "product_id": t.product_id,
            "qty": t.qty, "computed_total_paise": t.computed_total_paise,
            "status": t.status, "reason": t.reason,
            "razorpay_payment_id": t.razorpay_payment_id,
            "created_at": t.created_at.isoformat(),
        }
        for t in rows
    ]


@router.get("/catalog", response_model=list[ProductResponse])
def list_catalog(db: Session = Depends(get_db)):
    return db.query(Product).all()
