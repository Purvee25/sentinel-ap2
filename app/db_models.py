import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(String, index=True)
    name: Mapped[str] = mapped_column(String)
    price_paise: Mapped[int] = mapped_column(Integer)  # source of truth for price
    description: Mapped[str] = mapped_column(String, default="")


class Mandate(Base):
    __tablename__ = "mandates"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    merchant_id: Mapped[str] = mapped_column(String, index=True)
    max_amount_paise: Mapped[int] = mapped_column(Integer)
    nonce: Mapped[str] = mapped_column(String, unique=True, index=True)
    issued_at: Mapped[str] = mapped_column(String)  # ISO string, part of signed payload
    expires_at: Mapped[str] = mapped_column(String)
    signature_b64: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="active")  # active | consumed | expired
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    mandate_id: Mapped[str] = mapped_column(String, ForeignKey("mandates.id"), nullable=True)
    product_id: Mapped[str] = mapped_column(String, nullable=True)
    qty: Mapped[int] = mapped_column(Integer, default=0)
    computed_total_paise: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String)  # accepted | rejected
    reason: Mapped[str] = mapped_column(String, default="")
    razorpay_payment_id: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(String)
    payload_json: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
