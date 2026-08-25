"""Mandate creation and signature verification.

A mandate is a single-use spending authorization — cap, merchant, expiry —
signed by the user's key.
"""

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

from cryptography.exceptions import InvalidSignature

from app.keys import load_or_create_private_key, load_public_key


def _canonical_payload(mandate_id: str, merchant_id: str, max_amount_paise: int,
                        issued_at: str, expires_at: str, nonce: str) -> bytes:
    payload = {
        "mandate_id": mandate_id,
        "merchant_id": merchant_id,
        "max_amount_paise": max_amount_paise,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def create_signed_mandate(merchant_id: str, max_amount_paise: int, ttl_seconds: int) -> dict:
    mandate_id = str(uuid.uuid4())
    nonce = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    issued_at = now.isoformat()
    expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()

    payload = _canonical_payload(mandate_id, merchant_id, max_amount_paise, issued_at, expires_at, nonce)
    private_key = load_or_create_private_key()
    signature = private_key.sign(payload)

    return {
        "id": mandate_id,
        "merchant_id": merchant_id,
        "max_amount_paise": max_amount_paise,
        "nonce": nonce,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "signature_b64": base64.b64encode(signature).decode("ascii"),
    }


def verify_mandate_signature(mandate) -> bool:
    """Rebuilds the signed payload from the stored fields and checks it.

    Anything edited after issuance — a bumped cap, a swapped merchant —
    fails here.
    """
    payload = _canonical_payload(
        mandate.id, mandate.merchant_id, mandate.max_amount_paise,
        mandate.issued_at, mandate.expires_at, mandate.nonce,
    )
    public_key = load_public_key()
    try:
        public_key.verify(base64.b64decode(mandate.signature_b64), payload)
        return True
    except InvalidSignature:
        return False
