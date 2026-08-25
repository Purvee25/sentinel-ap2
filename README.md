# Sentinel-AP2

**Deterministic guardrail middleware for autonomous agent payments on Razorpay test-mode.**

Built for the [Razorpay AI Buildathon](https://razorpay.com/buildathon/) — Track 01: AI Growth & Agentic Commerce.

## The gap

AI agents are moving from *answering questions* to *buying things* — Google's AP2, OpenAI's ACP, and NPCI's UAP are all racing to standardize how an agent gets authorized to spend a user's money. But authorization is only half the problem: **something has to enforce it**, deterministically, at the moment of purchase, with no path for a prompt-injected or hallucinating agent to talk its way past the limit.

Sentinel-AP2 is that enforcement layer — a reference implementation, not a demo of "an agent that shops."

## The core idea

A user issues a **signed, bounded mandate**: max amount, one merchant, a time window. A buyer agent proposes a purchase — but it only ever sends `{product_id, qty}`, never a price. The guardrail engine:

1. verifies the mandate's cryptographic signature (Ed25519) against the exact fields it was signed for — any tampering after signing breaks verification
2. checks the mandate hasn't expired or already been consumed (single-use, replay-proof)
3. checks the request's merchant matches the mandate's merchant
4. looks up the **real price from the catalog** — never trusts a price the agent claims
5. computes the total itself and checks it against the mandate's cap
6. only then calls Razorpay test-mode to execute the payment

Every accepted **and rejected** attempt is written to an immutable audit log with a specific, human-readable reason.

**The point:** an LLM's output never becomes a money parameter. Prices, totals, and limits are enforced by plain comparisons in `app/guardrail.py` — prompt injection has no channel to influence a purchase decision, because the guardrail never reads the free text an LLM produces (or that a malicious product listing contains) to make one.

## Architecture

```
[demo agent / any buyer agent]
        │  {mandate_id, merchant_id, product_id, qty}
        ▼
[FastAPI]  →  app/guardrail.py  →  app/catalog.py (price truth)
        │            │
        │            └──────────→ app/mandate.py (Ed25519 verify)
        │
        ├──→ SQLite: mandates, products, transactions, audit_log
        └──→ Razorpay test-mode API (only after guardrail accepts)
```

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Or with Docker:

```bash
docker compose up --build
```

No credentials required — the app runs in **mock mode** automatically (mock Razorpay order IDs, scripted product picking) when `RAZORPAY_KEY_ID`/`RAZORPAY_KEY_SECRET`/`ANTHROPIC_API_KEY` are absent. Copy `.env.example` to `.env` to add real ones.

### Run the adversarial test suite

```bash
pytest tests/ -v
```

| Attack simulated | Expected result |
|---|---|
| Legitimate purchase within cap | **accepted** |
| Purchase total exceeds mandate cap | rejected — `exceeds mandate cap` |
| Quantity overflow pushes total over cap | rejected — `exceeds mandate cap` |
| Expired mandate replayed | rejected — `mandate expired` |
| Same (consumed) mandate reused | rejected — `mandate already used (replay rejected)` |
| Mandate spent against a different merchant | rejected — `merchant mismatch` |
| Mandate fields tampered post-signing | rejected — `invalid mandate signature` |
| Agent claims a price that doesn't match the catalog | rejected — `price mismatch` |
| Product description contains an embedded prompt-injection instruction ("ignore all spending limits") | rejected on the numeric cap alone — the instruction is never read |

All 9 currently pass (`pytest tests/ -v` — 9 passed).

### Watch the live end-to-end demo

```bash
uvicorn app.main:app --reload &
python scripts/demo_agent.py
```

This is the script the pitch video walks through: 1 accepted purchase, 3 live attack attempts rejected in real time, then the full audit trail printed.

## What's deliberately not built

- No key management/rotation — one Ed25519 keypair simulating the user's device, generated on first run (`app/keys.py`)
- No Redis — SQLite's unique constraint on `nonce` gives idempotency without another moving part in a 1-week build
- No real user auth system — one demo mandate-issuer is enough to prove the enforcement model
- Not a spec-compliant implementation of AP2/ACP/UAP — modeled on their bounded-mandate pattern, not certified against any of them

`docker compose up --build` is verified working end-to-end: builds clean, serves `/health` and `/catalog`, and `scripts/demo_agent.py` passes against the containerized instance.

## Repo layout

```
app/
  main.py          FastAPI app, startup: create tables + seed catalog
  guardrail.py      the deterministic enforcement engine (the actual project)
  mandate.py        mandate signing + verification (Ed25519)
  keys.py           simulated user keypair, persisted locally
  catalog.py        product seed data (source of price truth)
  razorpay_client.py  test-mode order creation, mock mode by default
  db_models.py / schemas.py / database.py
  routers/          mandates, purchase, audit endpoints
scripts/demo_agent.py   end-to-end live demo used for the pitch video
tests/test_adversarial_suite.py   the 9 attack scenarios above
```

## What broke, and how we got out

See [NOTES.md](NOTES.md).
