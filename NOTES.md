# What broke, and how we got out

Kept live during the build — this is the field the buildathon submission form weighs most heavily ("the last one is the one we read first").

## 1. `razorpay` SDK broke on Python 3.13

**What happened:** the moment `app.razorpay_client` imported the `razorpay` package, every route (and every test) failed with `ModuleNotFoundError: No module named 'pkg_resources'`. The SDK's HTTP client still imports `pkg_resources` for version checks, and modern `setuptools` no longer ships it by default on 3.13.

**First instinct (wrong):** just `pip install setuptools` and move on. Didn't fix it — newer setuptools versions still don't expose `pkg_resources` by default.

**Root cause:** this project runs in mock mode by default (no live Razorpay keys), so we were paying an import-time cost for a dependency the mock path never actually uses.

**Fix:** made the `razorpay` import lazy — it only happens inside `_get_client()`, which is only called when real credentials are present. Mock mode now has zero dependency on the SDK being importable at all. This is also just a better design: a project meant to run without credentials shouldn't hard-fail on an optional integration's transitive dependency.

## 2. Deciding what "expired" actually means for a signed mandate

**What happened:** the mandate schema rejects `ttl_seconds <= 0` at the API layer (correctly — you can't *issue* an already-expired mandate). But that made it impossible to test the expiry-rejection path through the public API.

**Options considered:** (a) sleep in the test until a very-short-TTL mandate expires — slow and flaky; (b) manually edit `expires_at` in the DB after issuance — but that breaks the signature too, which would make the test actually be testing signature tampering, not expiry.

**Fix:** the test calls `create_signed_mandate()` directly (bypassing the API's TTL validation, which is a request-shape concern, not a signing concern) with a negative TTL, producing a *validly signed* but already-expired mandate, then inserts it into the DB directly. This isolates "expired" from "tampered" as two genuinely separate, independently-tested failure modes — which also forced us to notice they're separate guardrail checks in the actual engine (expiry is checked before signature-independent logic, but both exist).

## 3. Docker build

Verified: `docker compose up --build` builds cleanly and serves real traffic — `/health`, `/catalog`, and the full `scripts/demo_agent.py` run (1 accepted purchase, 3 live attacks rejected) all pass against the containerized instance, not just the local venv.

## 4. Proving the guardrail holds against a compromised agent

**The problem:** the injected product description is only interesting if the agent might actually obey it. But an LLM's behaviour isn't deterministic — a test that runs a real model and asserts "the agent tried to overspend" would be flaky, and one recorded transcript where the model *resisted* the injection proves nothing about the guardrail.

**Resolution:** decouple the two claims. The agent (`app/agent.py`) is real and can be run live (`scripts/agent_demo.py --tempt`) to show what a manipulated agent does. But the *security* claim is tested against a simulated caller with total freedom over `{product_id, qty}` — which is strictly more adversarial than any single LLM transcript, and fully deterministic. `test_total_spend_never_exceeds_mandate_across_many_hostile_requests` throws every product at every quantity against one mandate and asserts total money moved stays under the cap.

That split is the actual thesis of the project: the guardrail's correctness must not depend on the agent staying uncompromised, so it shouldn't be *tested* through the agent either.

## What we'd do with another week

- Real AP2/ACP-schema compliance instead of a custom mandate format modeled on the pattern
- Multi-step mandates (spending caps across several purchases, not single-use)
- A minimal frontend showing live mandates/transactions/rejections instead of reading JSON off the audit endpoint
