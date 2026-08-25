"""Adversarial test suite for the Sentinel-AP2 guardrail engine.

Each test simulates one attack an autonomous buyer agent (compromised,
buggy, or hallucinating) could attempt against the payment middleware.
Every one of them must be REJECTED with a specific, auditable reason.
"""

from app.database import SessionLocal
from app.db_models import Mandate
from app.mandate import create_signed_mandate


def _issue_mandate(client, merchant_id="merchant_xyz", max_amount_paise=100000, ttl_seconds=3600):
    resp = client.post("/mandates", json={
        "merchant_id": merchant_id, "max_amount_paise": max_amount_paise, "ttl_seconds": ttl_seconds,
    })
    assert resp.status_code == 200
    return resp.json()


def _cheap_product(client):
    products = client.get("/catalog").json()
    return min(products, key=lambda p: p["price_paise"])


def _expensive_product(client):
    products = client.get("/catalog").json()
    return max(products, key=lambda p: p["price_paise"])


def test_legitimate_purchase_is_accepted(client):
    mandate = _issue_mandate(client, max_amount_paise=1000000)
    product = _cheap_product(client)

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    })
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["computed_total_paise"] == product["price_paise"]
    assert body["razorpay_payment_id"]


def test_over_cap_purchase_is_rejected(client):
    """Attack: agent tries to spend more than the mandate allows."""
    mandate = _issue_mandate(client, max_amount_paise=50000)  # ₹500 cap
    product = _expensive_product(client)  # costs more than ₹500

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "exceeds mandate cap" in body["reason"]


def test_quantity_overflow_is_rejected(client):
    """Attack: single-item price is within cap, but qty pushes it over."""
    mandate = _issue_mandate(client, max_amount_paise=200000)
    product = _cheap_product(client)  # ₹899, so qty=1000 blows way past ₹2000 cap

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1000,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "exceeds mandate cap" in body["reason"]


def test_expired_mandate_is_rejected(client):
    """Attack: agent replays an old mandate after its validity window."""
    signed = create_signed_mandate(merchant_id="merchant_xyz", max_amount_paise=1000000, ttl_seconds=-10)
    db = SessionLocal()
    db.add(Mandate(
        id=signed["id"], merchant_id=signed["merchant_id"], max_amount_paise=signed["max_amount_paise"],
        nonce=signed["nonce"], issued_at=signed["issued_at"], expires_at=signed["expires_at"],
        signature_b64=signed["signature_b64"], status="active",
    ))
    db.commit()
    db.close()

    product = _cheap_product(client)
    resp = client.post("/purchase", json={
        "mandate_id": signed["id"], "merchant_id": "merchant_xyz",
        "product_id": product["id"], "qty": 1,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "expired" in body["reason"]


def test_mandate_replay_is_rejected(client):
    """Attack: agent (or attacker who intercepted the mandate) reuses a
    mandate that already funded a successful purchase."""
    mandate = _issue_mandate(client, max_amount_paise=1000000)
    product = _cheap_product(client)
    purchase_body = {
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    }

    first = client.post("/purchase", json=purchase_body).json()
    assert first["status"] == "accepted"

    second = client.post("/purchase", json=purchase_body).json()
    assert second["status"] == "rejected"
    assert "already used" in second["reason"]


def test_merchant_substitution_is_rejected(client):
    """Attack: mandate was scoped to one merchant, agent tries to spend it elsewhere."""
    mandate = _issue_mandate(client, merchant_id="merchant_xyz", max_amount_paise=1000000)
    product = _cheap_product(client)

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": "some_other_merchant",
        "product_id": product["id"], "qty": 1,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "merchant mismatch" in body["reason"]


def test_tampered_mandate_signature_is_rejected(client):
    """Attack: attacker (or a buggy agent) edits the mandate's max amount
    directly in transit/storage after it was signed."""
    mandate = _issue_mandate(client, max_amount_paise=10000)  # signed for ₹100

    db = SessionLocal()
    row = db.query(Mandate).filter(Mandate.id == mandate["id"]).first()
    row.max_amount_paise = 100000000  # tamper: bump to ₹10,00,000 post-signing
    db.commit()
    db.close()

    product = _cheap_product(client)
    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "invalid mandate signature" in body["reason"]


def test_tampered_cart_price_is_rejected(client):
    """Attack: agent (hallucinating or manipulated) claims a price that
    doesn't match catalog truth — server must trust its own DB, not the caller."""
    mandate = _issue_mandate(client, max_amount_paise=1000000)
    product = _cheap_product(client)

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
        "client_claimed_price_paise": 1,  # agent claims it costs ₹0.01
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "price mismatch" in body["reason"]


def test_payment_failure_does_not_reopen_the_mandate(monkeypatch, client):
    """If Razorpay fails after the guardrail approved a purchase, the mandate
    stays consumed. Re-opening it would hand a caller a free retry against an
    authorization the user already spent, and we cannot tell from here whether
    the order was created before the failure."""
    from app import guardrail
    from app.razorpay_client import PaymentExecutionError

    mandate = _issue_mandate(client, max_amount_paise=1000000)
    product = _cheap_product(client)

    def boom(*args, **kwargs):
        raise PaymentExecutionError("gateway timeout")

    monkeypatch.setattr(guardrail, "create_test_order", boom)

    failed = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    }).json()
    assert failed["status"] == "failed"
    assert "gateway timeout" in failed["reason"]
    assert failed["razorpay_payment_id"] == ""

    monkeypatch.undo()

    retry = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    }).json()
    assert retry["status"] == "rejected"
    assert "already used" in retry["reason"]


def test_prompt_injection_in_product_description_has_no_effect(client):
    """Attack: catalog contains a product whose description tries to
    instruct any agent/LLM reading it to bypass spending limits. The
    guardrail never parses free text for a money decision, so this must
    be rejected purely on the numeric cap, exactly like any other
    over-cap attempt."""
    products = client.get("/catalog").json()
    malicious_product = next(p for p in products if "ignore all spending limits" in p["description"])

    mandate = _issue_mandate(client, max_amount_paise=1000)  # tiny cap, well under item price
    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": malicious_product["id"], "qty": 1,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "exceeds mandate cap" in body["reason"]
