from pydantic import BaseModel, Field


class MandateCreateRequest(BaseModel):
    merchant_id: str
    max_amount_paise: int = Field(gt=0)
    ttl_seconds: int = Field(gt=0, le=604800, description="Mandate validity window, max 7 days")


class MandateResponse(BaseModel):
    id: str
    merchant_id: str
    max_amount_paise: int
    nonce: str
    issued_at: str
    expires_at: str
    signature_b64: str
    status: str


class ProductResponse(BaseModel):
    id: str
    merchant_id: str
    name: str
    price_paise: int
    description: str


class PurchaseRequest(BaseModel):
    mandate_id: str
    merchant_id: str
    product_id: str
    qty: int = Field(gt=0)
    # Optional: what the calling agent *believes* the price is. Never trusted
    # for the actual charge — used only to detect a tampered/hallucinated cart.
    client_claimed_price_paise: int | None = None


class PurchaseResponse(BaseModel):
    status: str
    reason: str
    computed_total_paise: int
    razorpay_payment_id: str
    transaction_id: str


class AuditEntry(BaseModel):
    id: str
    event_type: str
    payload_json: str
    created_at: str
