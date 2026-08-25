"""Demo buyer-agent script for the pitch video.

Runs against a live Sentinel-AP2 server (default http://localhost:8000) and
walks through: (1) a legitimate purchase, (2) three live attack attempts,
each rejected with a clear reason.

If ANTHROPIC_API_KEY is set, an LLM is used to "decide" which product to
buy from a natural-language goal — but note its output only ever becomes a
{product_id, qty} pair; it never sees or sets a price. If the key is not
set, a scripted stand-in choice is used instead so this script always runs.
"""

import json
import os
import sys

import httpx

BASE_URL = os.getenv("SENTINEL_BASE_URL", "http://localhost:8000")


def _print_step(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


def _agent_pick_product(catalog: list[dict], goal: str) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("(No ANTHROPIC_API_KEY set — using scripted product choice.)")
        return min(catalog, key=lambda p: p["price_paise"])

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    catalog_text = "\n".join(f"- id={p['id']} name={p['name']} price_paise={p['price_paise']}" for p in catalog)
    message = client.messages.create(
        model="claude-sonnet-5",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"Goal: {goal}\n\nCatalog:\n{catalog_text}\n\n"
                "Reply with ONLY the product id of the single best match, nothing else."
            ),
        }],
    )
    chosen_id = message.content[0].text.strip()
    return next((p for p in catalog if p["id"] == chosen_id), catalog[0])


def main() -> None:
    client = httpx.Client(base_url=BASE_URL, timeout=10)

    _print_step("STEP 1: Fetch merchant catalog")
    catalog = client.get("/catalog").json()
    for p in catalog:
        print(f"  {p['name']:<28} ₹{p['price_paise'] / 100:.2f}   id={p['id']}")

    _print_step("STEP 2: User issues a bounded mandate (max ₹1500, 1 hour, merchant_xyz)")
    mandate = client.post("/mandates", json={
        "merchant_id": "merchant_xyz", "max_amount_paise": 150000, "ttl_seconds": 3600,
    }).json()
    print(json.dumps(mandate, indent=2))

    _print_step("STEP 3: Agent picks a product within budget and purchases")
    product = _agent_pick_product(catalog, goal="Buy me a wireless mouse or laptop stand, under ₹1500.")
    print(f"Agent selected: {product['name']} (₹{product['price_paise'] / 100:.2f})")
    result = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": "merchant_xyz",
        "product_id": product["id"], "qty": 1,
    }).json()
    print(json.dumps(result, indent=2))
    assert result["status"] == "accepted", "expected the legitimate purchase to succeed"

    _print_step("STEP 4: LIVE ATTACK 1 — replay the same (now consumed) mandate")
    replay = client.post("/purchase", json={
        "mandate_id": mandate["id"], "merchant_id": "merchant_xyz",
        "product_id": product["id"], "qty": 1,
    }).json()
    print(json.dumps(replay, indent=2))
    assert replay["status"] == "rejected"

    _print_step("STEP 5: LIVE ATTACK 2 — new mandate, but request exceeds its cap")
    small_mandate = client.post("/mandates", json={
        "merchant_id": "merchant_xyz", "max_amount_paise": 1000, "ttl_seconds": 3600,
    }).json()
    expensive = max(catalog, key=lambda p: p["price_paise"])
    overcap = client.post("/purchase", json={
        "mandate_id": small_mandate["id"], "merchant_id": "merchant_xyz",
        "product_id": expensive["id"], "qty": 1,
    }).json()
    print(f"Attempting to buy {expensive['name']} (₹{expensive['price_paise'] / 100:.2f}) "
          f"against a ₹10 mandate — note this product's description also contains an "
          f"embedded prompt-injection instruction telling any agent to ignore limits:")
    print(f"  \"{expensive['description']}\"\n")
    print(json.dumps(overcap, indent=2))
    assert overcap["status"] == "rejected"

    _print_step("STEP 6: LIVE ATTACK 3 — spend against the wrong merchant")
    wrong_merchant = client.post("/purchase", json={
        "mandate_id": small_mandate["id"], "merchant_id": "some_other_merchant",
        "product_id": product["id"], "qty": 1,
    }).json()
    print(json.dumps(wrong_merchant, indent=2))
    assert wrong_merchant["status"] == "rejected"

    _print_step("STEP 7: Full audit trail")
    audit = client.get("/audit").json()
    for entry in reversed(audit):
        print(f"  [{entry['event_type']}] {entry['payload_json']}")

    print("\nDemo complete: 1 accepted purchase, 3 live attacks rejected, full audit trail intact.")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print(f"Could not reach {BASE_URL}. Start the server first: uvicorn app.main:app --reload")
        sys.exit(1)
