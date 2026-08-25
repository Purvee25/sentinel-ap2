# What broke, and how we got out

Kept live during the build — this is the field the buildathon submission form weighs most heavily ("the last one is the one we read first").

## 0. The `pkg_resources` bug we thought we'd fixed, and hadn't

**What happened:** `razorpay` 1.4.2 imports `pkg_resources` at module load, which no longer exists — `setuptools` removed it in v81 (this machine has 84). Early in the build this broke every import, and the "fix" was to make the razorpay import lazy so mock mode didn't need it (see below). That worked, and the test suite went green.

Then real credentials got added, the live path executed for the first time, and the exact same error came straight back. The lazy import hadn't fixed the bug — it had **deferred it to the one code path that mattered**, and the green test suite made it look solved. Nothing tested the live branch, so nothing caught it.

**Actual fix:** upgraded to `razorpay` 2.0.1, which dropped the `pkg_resources` dependency. Verified `order.create` and `set_app_details` still exist on the 2.x client before pinning it.

**Second bug this exposed:** the failure surfaced to the user as *"Check the key id/secret are correct"* — because `create_test_order` wrapped everything, including import errors, in `PaymentExecutionError`. A missing module was being reported as bad credentials, which sends you debugging the wrong thing entirely. The client construction now happens outside the try block, and `verify_razorpay.py` handles `ImportError` separately with the right advice.

**Lesson:** making an error go away is not the same as fixing it, and a passing test suite only covers the paths it actually exercises.

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

## 5. `.env` was in requirements but never loaded

**What happened:** `python-dotenv` was pinned in `requirements.txt` and `.env.example` told users to copy it to `.env` — but nothing ever called `load_dotenv()`. Anyone following the README would have added correct Razorpay keys, seen `order_mock_*` ids anyway, and had no idea why. A silent config failure, which is the worst kind.

**Fix:** added `app/config.py`, which loads `.env` on import and reads credentials **lazily** rather than capturing them at import time — so import order stops mattering and tests can override credentials without reimporting modules.

**Related fix:** `scripts/verify_razorpay.py` refuses to run against any key not starting with `rzp_test_`. Nothing in this project should be one typo away from touching real money.

## 6. What happens when the payment fails *after* the guardrail says yes

The mandate is marked consumed *before* the Razorpay call, so a crash mid-payment can't be replayed. But that raised a question the original code ignored: if Razorpay then fails, is the mandate re-opened?

**Decision: no.** Re-opening it would hand a caller a free retry against an authorization the user already spent — and from inside the process we cannot tell whether the order was created before the failure surfaced. The purchase is recorded with status `failed`, an audit entry is written, and the user re-issues a mandate to try again. `test_payment_failure_does_not_reopen_the_mandate` pins this behaviour.

This is the graceful-failure path the track brief asks for: it fails closed, it's auditable, and the reasoning is written down rather than implied.

## 7. Adding real credentials silently turned the test suite live

**What happened:** once a `.env` with real Razorpay keys existed, `app.config` loaded it for *every* process — including pytest. The suite would have created a live test-mode order on each accepted purchase: junk in the dashboard, and tests newly dependent on the network.

**Fix:** `conftest.py` forces mock mode. It sets the credential vars to empty strings rather than deleting them, because `load_dotenv()` repopulates absent keys from `.env` but leaves existing ones alone — deleting them would have been silently undone.

The reasoning: payment *execution* isn't what these tests are about. They test the guardrail's decisions, and those are identical in either mode.

## 8. What is *not* verified

Worth stating outright rather than letting someone discover it:

- **The agent's LLM loop has never executed.** No Anthropic API key was available during the build. Its tools, argument validation, and guardrail integration are all tested and working against a live server; the `client.messages.create` call is written from current SDK documentation but unexercised. The security claim it illustrates is proven separately and deterministically in `tests/test_compromised_agent.py`, which is why this gap is a presentation weakness rather than a correctness one.
- **Scope is deliberately narrow:** five products, one merchant, single-use mandates, one user. This is a reference implementation of the enforcement layer, not a product.

## What we'd do with another week

- Real AP2/ACP-schema compliance instead of a custom mandate format modeled on the pattern
- Multi-step mandates (spending caps across several purchases, not single-use)
- A minimal frontend showing live mandates/transactions/rejections instead of reading JSON off the audit endpoint
