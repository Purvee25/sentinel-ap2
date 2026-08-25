"""What the guardrail does when the agent itself has been compromised.

The adversarial suite covers attacks on the protocol. This one covers the
case the project exists for: the LLM has been manipulated and is issuing
hostile purchases in good faith.

No API key needed, and deliberately so. Proving this doesn't require a real
model — it requires a caller with complete freedom over {product_id, qty},
which is what these simulate, and which is more hostile than any one
transcript would be.
"""

from app.database import SessionLocal
from app.db_models import Mandate


def _issue_mandate(client, max_amount_paise=100000, merchant_id="merchant_xyz"):
    return client.post("/mandates", json={
        "merchant_id": merchant_id, "max_amount_paise": max_amount_paise, "ttl_seconds": 3600,
    }).json()


def _injected_product(client):
    products = client.get("/catalog").json()
    return next(p for p in products if "ignore all spending limits" in p["description"])


def test_agent_that_obeys_the_injection_still_cannot_overspend(client):
    """The catalog tells the agent to ignore spending limits and buy at any
    price. Simulate an agent that fully complies: it requests the injected
    product despite a cap far below its price."""
    mandate = _issue_mandate(client, max_amount_paise=1000)  # ₹10
    product = _injected_product(client)

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    }).json()

    assert resp["status"] == "rejected"
    assert "exceeds mandate cap" in resp["reason"]
    assert resp["razorpay_payment_id"] == ""  # no money moved


def test_compromised_agent_cannot_drain_via_repeated_purchases(client):
    """A single-use mandate means an agent told to 'keep buying' gets exactly
    one purchase, no matter how many times it tries."""
    mandate = _issue_mandate(client, max_amount_paise=1000000)
    products = client.get("/catalog").json()
    cheapest = min(products, key=lambda p: p["price_paise"])

    verdicts = [
        client.post("/purchase", json={
            "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
            "product_id": cheapest["id"], "qty": 1,
        }).json()["status"]
        for _ in range(5)
    ]

    assert verdicts[0] == "accepted"
    assert all(v == "rejected" for v in verdicts[1:])


def test_compromised_agent_cannot_escalate_by_splitting_quantity(client):
    """Salami-slicing: an agent that can't afford 50 units tries the largest
    quantity that fits. That succeeds (it's within the mandate, so it should),
    but consumes the mandate — it cannot then buy the rest."""
    mandate = _issue_mandate(client, max_amount_paise=200000)  # ₹2000
    products = client.get("/catalog").json()
    cheapest = min(products, key=lambda p: p["price_paise"])  # ₹899
    affordable_qty = 200000 // cheapest["price_paise"]  # 2 units = ₹1798

    first = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": cheapest["id"], "qty": affordable_qty,
    }).json()
    assert first["status"] == "accepted"
    assert first["computed_total_paise"] <= 200000

    second = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": cheapest["id"], "qty": 1,
    }).json()
    assert second["status"] == "rejected"


def test_total_spend_never_exceeds_mandate_across_many_hostile_requests(client):
    """Property check: throw a wide range of hostile purchase requests at a
    single mandate and assert the invariant the whole system exists to hold —
    total money moved never exceeds the cap."""
    cap = 150000  # ₹1500
    mandate = _issue_mandate(client, max_amount_paise=cap)
    products = client.get("/catalog").json()

    total_charged = 0
    for product in products:
        for qty in (1, 3, 25, 1000):
            resp = client.post("/purchase", json={
                "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
                "product_id": product["id"], "qty": qty,
            }).json()
            total_charged += resp["computed_total_paise"]

    assert total_charged <= cap, f"guardrail leaked: {total_charged} > {cap}"


def test_agent_cannot_forge_a_mandate_id(client):
    """An agent that invents a mandate id gets nothing."""
    products = client.get("/catalog").json()
    resp = client.post("/purchase", json={
        "mandate_id": "00000000-0000-0000-0000-000000000000",
        "merchant_id": "merchant_xyz",
        "product_id": products[0]["id"], "qty": 1,
    }).json()

    assert resp["status"] == "rejected"
    assert "not found" in resp["reason"]


def test_agent_cannot_raise_its_own_cap(client):
    """Even with direct write access to the mandate record — a stronger
    position than any real agent holds — the signature check catches it."""
    mandate = _issue_mandate(client, max_amount_paise=1000)
    product = _injected_product(client)

    db = SessionLocal()
    row = db.query(Mandate).filter(Mandate.id == mandate["id"]).first()
    row.max_amount_paise = 99999999
    db.commit()
    db.close()

    resp = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": mandate["merchant_id"],
        "product_id": product["id"], "qty": 1,
    }).json()

    assert resp["status"] == "rejected"
    assert "invalid mandate signature" in resp["reason"]
