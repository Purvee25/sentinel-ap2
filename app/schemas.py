from pydantic import BaseModel, Field

# Pydantic models = typed request/response contracts; validation is automatic


class MandateCreateRequest(BaseModel):
    merchant_id: str
    max_amount_paise: int = Field(gt=0)                      # spending cap in paise (100 paise = ₹1)
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
    # agent's claimed price — NEVER used for the charge; only compared to catalog to detect tampering
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
