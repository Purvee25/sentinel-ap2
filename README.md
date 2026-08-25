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

## Dashboard

With the app running, open **http://localhost:8000/** — an operator dashboard where you can issue a mandate, fire a legitimate agent purchase, and launch each attack against it, watching the guardrail's verdicts land in a live audit trail (green accepted, red rejected with the reason in plain English).

API docs are at **http://localhost:8000/docs**.

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

## The autonomous agent

`app/agent.py` is a real tool-calling agent (Claude, `claude-opus-5`) that shops against the catalog: an explicit control loop with a hard 12-step cap, two tools (`search_catalog`, `purchase`), and validation on every tool argument since they originate from model output. It is a **client** of the guardrail, not part of it.

```bash
python scripts/agent_demo.py           # a goal the agent can satisfy within its mandate
python scripts/agent_demo.py --tempt   # a goal that steers it toward the injected product
```

Requires `ANTHROPIC_API_KEY`. The guardrail and its tests need no key — only the agent uses an LLM.

**The design point:** the agent's purchase tool takes `product_id` and `qty`. It cannot state a price, cannot raise its own cap, and cannot reuse a mandate. One product's description contains an instruction telling any agent reading it to ignore spending limits — so the agent may well be compromised, and the system is built so that this does not matter. `tests/test_compromised_agent.py` proves the invariant directly by simulating a fully-obedient hostile caller: across every product and quantity thrown at one mandate, total money moved never exceeds the cap.

### Run the test suites

```bash
pytest tests/ -v
```

15 tests: 9 protocol attacks (`test_adversarial_suite.py`) and 6 compromised-agent scenarios (`test_compromised_agent.py`).

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

And the compromised-agent suite:

| Scenario | Expected result |
|---|---|
| Agent obeys the injection and buys the expensive item anyway | rejected on cap; no money moved |
| Agent told to "keep buying" fires five purchases at one mandate | exactly one accepted, four rejected |
| Agent salami-slices to the largest affordable quantity, then tries again | first accepted within cap, second rejected |
| Every product × every quantity thrown at one mandate | total charged never exceeds the cap |
| Agent invents a mandate id | rejected — not found |
| Agent rewrites its own cap in the database | rejected — invalid signature |

### Watch the live end-to-end demo

```bash
./run_demo.sh
```

Wipes prior state, starts the container, waits for health, and runs the buyer-agent demo end to end: 1 accepted purchase, 3 live attack attempts rejected in real time, then the full audit trail printed (exactly 4 entries — one clean run).

To run it against an already-running server instead:

```bash
python scripts/demo_agent.py
```

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
