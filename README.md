# Sentinel-AP2

A guardrail layer that sits between an AI agent and a payment gateway, so the agent can spend money without being trusted.

Built for the [Razorpay AI Buildathon](https://razorpay.com/buildathon/), Track 01.

---

**One session, five purchase requests, one ₹1,500 mandate:**

```
agent attempted    ₹3,03,546.00     5 requests
blocked            ₹3,02,647.00     4 refused at the guardrail
actually charged   ₹      899.00     the one legitimate purchase
```

Each rejection died at a different check, and nothing downstream of it ran:

```
                    signature  single-use  expiry  merchant  price   cap    pay
agent purchase          ✓          ✓         ✓        ✓        ✓      ✓     ₹899
replayed mandate        ✓          ✕         ·        ·        ·      ·      ·
50 × earbuds            ✓          ✓         ✓        ✓        ✓      ✕      ·
merchant swap           ✓          ✓         ✓        ✕        ·      ·      ·
price tampering         ✓          ✓         ✓        ✓        ✕      ·      ·
```

The `50 × earbuds` row is the interesting one. That product's description contains an
instruction telling any agent reading it to ignore its spending limits. It failed on
arithmetic — `29995000 > 150000` — without anything having read the text.

---

## Why

AI agents are starting to buy things, not just answer questions. AP2, ACP and NPCI's UAP are all working out how an agent gets *authorized* to spend your money.

Authorization is the easier half. Something still has to *enforce* the limit at the moment of purchase, and it has to work even when the agent has been prompt-injected, has hallucinated a price, or is straightforwardly compromised. That's what this is.

It isn't an agent that shops. It's the thing that decides whether the agent is allowed to.

## How it works

You issue a signed mandate: a spending cap, one merchant, a time window. An agent proposes a purchase, but all it can send is `{product_id, qty}` — never a price.

The guardrail then:

1. verifies the Ed25519 signature against the exact fields that were signed, so any edit after issuance fails
2. checks the mandate isn't expired or already used (single-use)
3. checks the merchant matches
4. looks the price up in its own catalog, ignoring anything the caller claims
5. computes the total and compares it to the cap
6. only then calls Razorpay

Every attempt, accepted or rejected, gets an audit entry with a reason.

The reason prompt injection doesn't work here isn't that I filter for it. It's that no text an LLM produces — or that a malicious product listing contains — is ever read to make a money decision. Prices come from the database, totals are computed server-side, limits are plain integer comparisons in `app/guardrail.py`.

**The LLM-free zone:** mandate signature verification, catalog price lookup, cap arithmetic, and Razorpay order creation are all deterministic code. No LLM touches any money decision. The only LLM in the project is `app/agent.py` — the buyer, not the guardrail. This means the security property holds regardless of model behavior.

```
agent  ──{mandate_id, merchant_id, product_id, qty}──▶  FastAPI
                                                          │
                                        guardrail.py ◀────┤
                                          ├─ mandate.py   (Ed25519 verify)
                                          ├─ catalog.py   (price truth)
                                          ├─ SQLite       (mandates, txns, audit)
                                          └─ Razorpay     (only after accept)
```

## What's actually new here

Signed mandates and single-use nonces aren't novel — they're standard patterns, and the crypto is about forty lines. What I'd point at instead:

**Injection is impossible rather than filtered.** The usual defense is a classifier looking for "ignore your instructions", which loses to rephrasing. There's no filter here because there's no channel: the agent sends two values, neither of them a price, and every money decision downstream is integer comparison. The catalog ships with a product whose description tells agents to ignore spending limits, and it gets rejected on arithmetic without anything having read it.

**The security property is proven, not demonstrated.** Most projects show a guardrail working once. `test_total_spend_never_exceeds_mandate_across_many_hostile_requests` throws every product at every quantity against one mandate and asserts total money moved stays under the cap — no LLM in the loop, deliberately, since a test that needs a model to misbehave on cue isn't a test. The claim isn't "my agent behaved." It's that it doesn't matter whether it does.

**Correctness doesn't assume a cooperative agent.** Limits are usually a safety net over an agent you basically trust. Here the agent is assumed hostile from the start and tested that way: obeying the injection, told to keep buying, salami-slicing quantities, forging a mandate id, rewriting its own cap directly in the database.

**Failure after approval has a decided answer.** If Razorpay fails once the guardrail has said yes, the mandate stays consumed rather than reopening — a timeout means you can't tell whether the order was created, and reopening grants a retry against authorization already spent. Fails closed, and `test_payment_failure_does_not_reopen_the_mandate` keeps it that way.

**Rejections are records, not errors.** Every blocked attempt writes a transaction row and an audit entry with its reason, so the trail shows what was stopped rather than only what succeeded.

## Running it

```bash
docker compose up --build
```

Then open http://localhost:8000/ for the console, or `/docs` for the API.

Locally without Docker:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

No credentials needed. Without Razorpay keys it runs in mock mode and returns `order_mock_*` ids.

### With real Razorpay test keys

Generate a test key at [dashboard.razorpay.com](https://dashboard.razorpay.com/) (Test Mode → Settings → API Keys), then:

```bash
cp .env.example .env    # paste the key id and secret in
python scripts/verify_razorpay.py
```

That creates one ₹1.00 order so you can match the id in the dashboard under Transactions → Orders. The script won't run against anything that isn't an `rzp_test_*` key.

Confirmed working against live test mode — accepted purchases come back with real order ids like `order_TU2KIC2lKpldbt`, rejected ones with an empty payment id, because no order is ever created for them.

Tests always run in mock mode even when keys are present, so they don't fill your dashboard with junk. See `tests/conftest.py`.

## The console

http://localhost:8000/ gives you a panel to issue a mandate, run a legitimate purchase, and fire each attack at it, with the guardrail's verdicts streaming into an audit log. Useful for seeing the thing work without reading JSON.

## The agent

`app/agent.py` is a tool-calling agent on `claude-opus-5` with a 12-step cap and two tools, `search_catalog` and `purchase`. It's a client of the guardrail, not part of it.

```bash
python scripts/agent_demo.py           # goal it can satisfy
python scripts/agent_demo.py --tempt   # goal that walks it into the injected product
```

Needs `ANTHROPIC_API_KEY`. Nothing else in the project does.

One product's description contains an instruction telling any agent reading it to ignore spending limits, so the agent may well end up compromised. That's deliberate, and it doesn't matter — its purchase tool can't state a price, raise a cap, or reuse a mandate.

**Caveat worth stating up front:** the agent's LLM loop has never actually run. I built this without an Anthropic key. The tools, argument validation and guardrail integration are all tested against a live server, but `client.messages.create` itself is unexercised, so you may hit a rough edge. The security property it demonstrates doesn't depend on it — see below.

## Tests

```bash
pytest tests/ -v
```

17 tests. Ten cover attacks on the protocol:

| Attack | Result |
|---|---|
| Legitimate purchase within cap | accepted |
| Total exceeds the cap | rejected — exceeds mandate cap |
| Quantity pushes total over the cap | rejected — exceeds mandate cap |
| Expired mandate | rejected — mandate expired |
| Consumed mandate reused | rejected — replay |
| Spent against a different merchant | rejected — merchant mismatch |
| Mandate fields edited after signing | rejected — invalid signature |
| Caller claims a price the catalog disagrees with | rejected — price mismatch |
| Product description carries an injection payload | rejected on the cap; the text is never read |
| Razorpay fails after the guardrail approved | recorded as failed, mandate stays consumed |

Six cover the agent itself being hostile:

| Scenario | Result |
|---|---|
| Agent obeys the injection and buys anyway | rejected on cap, no money moved |
| Agent told to keep buying, fires five purchases | one accepted, four rejected |
| Agent buys the largest affordable quantity, then tries again | first accepted, second rejected |
| Every product × every quantity against one mandate | total charged never exceeds the cap |
| Agent invents a mandate id | rejected — not found |
| Agent rewrites its own cap in the database | rejected — invalid signature |

That last group is where the actual security argument lives. Rather than running a real LLM and hoping it misbehaves on cue, those tests simulate a caller with complete freedom over the purchase payload, which is more hostile than any single model run and doesn't depend on model behaviour staying constant.

One more test, `test_fuzz_invariant.py`, runs a small slice of the fuzzer below on every `pytest` call, so the invariant it checks can't silently break between real fuzz runs.

## Measured at scale

The 17 tests above check specific, named attacks. This checks the general case:

```bash
python scripts/fuzz.py --attempts 50000
```

Fires 50,000 randomly generated hostile purchase requests — random product, random quantity up to several thousand units, random merchant, random claimed price, random or forged mandate ids — directly at the guardrail, and checks one thing after every single one: did any mandate's cap get exceeded.

**Latest run: 50,000 attempts, 0 cap violations.** ~₹546 crore in intended spend across every attempt, ~₹33 lakh actually charged — all of it the legitimate purchases. Full numbers and the committed output: [`MEASUREMENTS.md`](MEASUREMENTS.md).

## One-command demo

```bash
./run_demo.sh
```

Wipes state, starts the container, waits for health, runs the walkthrough: one accepted purchase, three attacks rejected, then the audit trail.

## What I left out

- No key rotation. One Ed25519 keypair standing in for the user's device, generated on first run.
- No Redis. A unique constraint on `nonce` in SQLite gives idempotency without another service to run.
- No auth system. A single mandate issuer is enough to demonstrate enforcement.
- Not AP2/ACP/UAP spec-compliant. The mandate format is modeled on their bounded-authorization pattern, not certified against any of them.
- Five products, one merchant, single-use mandates. Narrow on purpose.

## What broke

- **`pkg_resources` missing from modern setuptools** — `razorpay` 1.4.2 imports it at module load; setuptools 81+ dropped it entirely. Lazy-importing the SDK hid the crash until real credentials were added, at which point it resurfaced in the only live branch with a misleading "bad credentials" message (client construction was inside the `PaymentExecutionError` try block). → Upgraded to `razorpay` 2.0.1, moved client construction outside the error wrapper, and added a separate `ImportError` catch in `verify_razorpay.py`.
- **Razorpay fails closed — mandate consumed even if the payment errors** — if Razorpay fails after the guardrail has said yes, reopening the mandate would hand the caller a free retry against authorization already spent; from inside the process there's no way to know whether the order was actually created before the failure. → The mandate stays consumed; the purchase is recorded as `failed` with an audit entry; the user issues a new mandate to retry. `test_payment_failure_does_not_reopen_the_mandate` locks this in.
- **Prompt injection in catalog data (earbuds)** — one product's description tells any agent reading it to ignore spending limits. The agent may well obey. → Irrelevant: the guardrail never reads product descriptions. The purchase fails on `29995000 > 150000` — integer arithmetic, no text involved.
- **`/simulate` (and `/run`) endpoint returning 500** — the live path was never exercised until real Razorpay keys were added, because mock mode swallowed the import error. A green test suite against mock mode gave no signal about the live branch. → Fixed by the SDK upgrade and by forcing `conftest.py` to blank out credential vars so tests always run mock, keeping the suite honest.
- **`.env` loaded but never read** — `python-dotenv` was pinned and `.env.example` was documented, but nothing called `load_dotenv()`. Anyone following the README would have pasted in correct keys and kept seeing `order_mock_*` ids with no explanation. → Moved config into `app/config.py`, which loads `.env` at startup and reads credentials lazily so import ordering doesn't matter.
- **Adding credentials silently turned the test suite live** — once a real `.env` existed, `app.config` loaded it for every process including pytest, creating live test-mode orders on every accepted purchase. → `conftest.py` now forces mock mode by setting credential vars to empty strings (not deleting them — `load_dotenv()` skips keys that already exist in the environment, so deletion would have been silently undone).

## Layout

```
app/
  guardrail.py        the enforcement engine — the actual project
  mandate.py          signing and verification
  keys.py             the stand-in user keypair
  catalog.py          seed products, and the source of price truth
  razorpay_client.py  order creation, mock by default
  agent.py            the buyer agent
  config.py           env/.env handling
  main.py, db_models.py, schemas.py, database.py, routers/
scripts/
  demo_agent.py       scripted walkthrough
  agent_demo.py       live LLM agent
  verify_razorpay.py  credential check
tests/
```

[NOTES.md](NOTES.md) has what broke while building it.
