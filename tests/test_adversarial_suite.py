"""Adversarial test suite for the Sentinel-AP2 guardrail engine.

Each test simulates one attack an autonomous buyer agent (compromised,
buggy, or hallucinating) could attempt against the payment middleware.
Every one of them must be REJECTED with a specific, auditable reason.

ADVERSARIAL TDD: these tests were written BEFORE the enforcement code in
guardrail.py — the guardrail was built to make them pass, not the other way.
"""

from app.database import SessionLocal
from app.db_models import Mandate
from app.mandate import create_signed_mandate


# --- helpers ----------------------------------------------------------------

def _issue_mandate(client, merchant_id="merchant_xyz", max_amount_paise=100000, ttl_seconds=3600):
    # create a signed spending permission via the API
    resp = client.post("/mandates", json={
        "merchant_id": merchant_id, "max_amount_paise": max_amount_paise, "ttl_seconds": ttl_seconds,
    })
    assert resp.status_code == 200
    return resp.json()


def _cheap_product(client):
    # cheapest catalog item — always within a reasonable cap
    products = client.get("/catalog").json()
    return min(products, key=lambda p: p["price_paise"])


def _expensive_product(client):
    # most expensive catalog item — used to trigger cap violations
    products = client.get("/catalog").json()
    return max(products, key=lambda p: p["price_paise"])


# --- tests ------------------------------------------------------------------

def test_legitimate_purchase_is_accepted(client):
    # HAPPY PATH — valid mandate + correct merchant + within cap → accepted
    mandate = _issue_mandate(client, max_amount_paise=1000000)  # ₹10,000 cap
    product = _cheap_product(client)

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    })
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["computed_total_paise"] == product["price_paise"]  # server used catalog price
    assert body["razorpay_payment_id"]  # Razorpay actually processed it


def test_over_cap_purchase_is_rejected(client):
    # ATTACK: agent tries to buy something worth more than mandate allows
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
    # ATTACK: single item price looks fine, but large qty pushes total over cap
    mandate = _issue_mandate(client, max_amount_paise=200000)  # ₹2,000 cap
    product = _cheap_product(client)  # ₹899 each — qty=1000 → ₹8,99,000, way over cap

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1000,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "exceeds mandate cap" in body["reason"]


def test_expired_mandate_is_rejected(client):
    # ATTACK: agent replays an old mandate after its TTL window has passed
    # ttl_seconds=-10 creates a mandate already expired at creation time
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
    # ATTACK: reuse a mandate that already funded one purchase
    # first purchase succeeds; second with same mandate_id must be blocked
    mandate = _issue_mandate(client, max_amount_paise=1000000)
    product = _cheap_product(client)
    purchase_body = {
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    }

    first = client.post("/purchase", json=purchase_body).json()
    assert first["status"] == "accepted"  # first use: OK

    second = client.post("/purchase", json=purchase_body).json()
    assert second["status"] == "rejected"  # mandate already consumed
    assert "already used" in second["reason"]


def test_merchant_substitution_is_rejected(client):
    # ATTACK: mandate issued for merchant_xyz, but request targets a different merchant
    mandate = _issue_mandate(client, merchant_id="merchant_xyz", max_amount_paise=1000000)
    product = _cheap_product(client)

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": "some_other_merchant",  # wrong merchant
        "product_id": product["id"], "qty": 1,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "merchant mismatch" in body["reason"]


def test_tampered_mandate_signature_is_rejected(client):
    # ATTACK: directly edit the DB row to raise cap AFTER the mandate was signed
    # Ed25519 re-verifies on every purchase — tampered field breaks the signature
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
    assert "invalid mandate signature" in body["reason"]  # Ed25519 caught the tamper


def test_tampered_cart_price_is_rejected(client):
    # ATTACK: agent claims product costs ₹0.01 (hallucinated or injected price)
    # server ignores client_claimed_price_paise and uses catalog; mismatch = rejection
    mandate = _issue_mandate(client, max_amount_paise=1000000)
    product = _cheap_product(client)

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
        "client_claimed_price_paise": 1,  # agent claims ₹0.01; catalog says much more
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "price mismatch" in body["reason"]


def test_payment_failure_does_not_reopen_the_mandate(monkeypatch, client):
    # CONSUME-BEFORE-PAYMENT test: mandate marked consumed BEFORE Razorpay call
    # if gateway crashes, mandate stays consumed — retry blocked as "already used"
    # this prevents double-charge via failure + retry
    from app import guardrail
    from app.razorpay_client import PaymentExecutionError

    mandate = _issue_mandate(client, max_amount_paise=1000000)
    product = _cheap_product(client)

    def boom(*args, **kwargs):
        raise PaymentExecutionError("gateway timeout")

    monkeypatch.setattr(guardrail, "create_test_order", boom)  # simulate Razorpay crash

    failed = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    }).json()
    assert failed["status"] == "failed"
    assert "gateway timeout" in failed["reason"]
    assert failed["razorpay_payment_id"] == ""  # no payment ID — Razorpay never responded

    monkeypatch.undo()

    # retry attempt — mandate is still consumed even though payment failed
    retry = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    }).json()
    assert retry["status"] == "rejected"
    assert "already used" in retry["reason"]  # at-most-once guarantee holds


def test_prompt_injection_in_product_description_has_no_effect(client):
    # ATTACK: catalog product description says "ignore all spending limits"
    # guardrail never reads free text for money decisions — rejects on numeric cap only
    products = client.get("/catalog").json()
    malicious_product = next(p for p in products if "ignore all spending limits" in p["description"])

    mandate = _issue_mandate(client, max_amount_paise=1000)  # tiny cap, well under item price
    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": malicious_product["id"], "qty": 1,
    })
    body = resp.json()
    assert body["status"] == "rejected"
    assert "exceeds mandate cap" in body["reason"]  # rejected on cap, not on text content
