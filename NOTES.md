# What broke

Notes I kept while building this. Roughly in the order things happened.

## setuptools removed pkg_resources and I didn't really fix it

First thing that went wrong. Importing `app.razorpay_client` blew up every route and every test with `ModuleNotFoundError: No module named 'pkg_resources'`. The razorpay SDK (1.4.2) imports it at module load for version checks. setuptools dropped it in v81; this machine has 84.

I tried `pip install setuptools`, which did nothing, because the module is gone from modern setuptools entirely rather than just missing.

What I did instead was make the razorpay import lazy, so it only runs inside `_get_client()` when real credentials exist. Mock mode stopped touching the SDK at all. Tests went green and I moved on.

That was not a fix. It was the bug moved somewhere I wasn't looking.

Days later I added real Razorpay keys, ran the live path for the first time, and got the identical error back. The lazy import had pushed the failure into the one branch that actually mattered, and because nothing tested that branch, a fully green suite told me it was solved.

The real fix was upgrading to razorpay 2.0.1, which dropped the dependency. I checked `order.create` and `set_app_details` still existed on the 2.x client before pinning it, since a major version bump can move things.

The same incident surfaced a second problem. The error reached me as "Check the key id/secret are correct" — my own message — because `create_test_order` had the client construction inside the try block that wraps everything in `PaymentExecutionError`. So a missing module got reported as bad credentials, and I spent a while looking at my keys instead of my dependencies. Client construction now sits outside that block, and `verify_razorpay.py` catches `ImportError` separately.

Two things I took from this. Making an error stop appearing isn't the same as fixing it. And a green test suite only tells you about the paths it runs.

## Testing "expired" without accidentally testing "tampered"

The mandate schema rejects `ttl_seconds <= 0`, which is right — you shouldn't be able to issue a mandate that's already dead. But it also meant I couldn't reach the expiry-rejection path through the API.

Sleeping in a test until a short-TTL mandate expired would be slow and flaky. Editing `expires_at` in the database after issuance breaks the signature, so the test would pass for the wrong reason: it'd be exercising signature verification, not expiry.

I ended up calling `create_signed_mandate()` directly with a negative TTL and inserting the row myself. That produces a validly signed mandate that's already expired, which is exactly the case I wanted. Writing it forced me to notice that expiry and tampering are two separate checks in the engine, and worth testing separately.

## What happens if the payment fails after the guardrail says yes

The mandate gets marked consumed before the Razorpay call, so a crash mid-payment can't be replayed. Fine. But that left a question I'd skipped: if Razorpay itself then fails, do I reopen the mandate?

I decided no. Reopening hands the caller a free retry against an authorization the user already spent, and from inside the process I can't tell whether the order was created before the failure surfaced. So it fails closed: the purchase is recorded as `failed`, an audit entry is written, and the user issues a new mandate if they want to try again. `test_payment_failure_does_not_reopen_the_mandate` locks that in.

I'd rather have the annoying-but-safe behaviour here than the convenient one.

## .env was in requirements but never loaded

`python-dotenv` was pinned and `.env.example` told you to copy it to `.env`, but nothing ever called `load_dotenv()`. Anyone following my own README would have pasted in correct keys, kept seeing `order_mock_*` ids, and had no idea why.

Silent config failures are the worst kind because nothing looks wrong. Added `app/config.py`, which loads `.env` and reads credentials lazily instead of capturing them at import time, so import ordering stops mattering.

While I was there I made `verify_razorpay.py` refuse any key that doesn't start with `rzp_test_`. Nothing in this project should be one typo from touching real money.

## Adding credentials quietly turned my test suite live

Immediately after the above. Once a real `.env` existed, `app.config` loaded it for every process including pytest, which meant the suite would create a live test-mode order on every accepted purchase. Junk in the dashboard, and tests that suddenly needed the network.

`conftest.py` now forces mock mode. It sets the credential vars to empty strings rather than deleting them, because `load_dotenv()` fills in absent keys but leaves existing ones alone — deleting them would have been silently undone on the next import.

These tests are about the guardrail's decisions, and those don't change between mock and live.

## Proving the guardrail works when the agent doesn't

One of the products has a description telling any agent reading it to ignore spending limits. That's only interesting if the agent might actually obey.

But LLM behaviour isn't deterministic. A test that runs a real model and asserts "the agent tried to overspend" would be flaky, and a single transcript where the model happened to resist proves nothing about the guardrail.

So I split the two claims. The agent in `app/agent.py` is real and you can run it (`scripts/agent_demo.py --tempt`) to see what a manipulated one does. The security claim is tested separately against a simulated caller with complete freedom over `{product_id, qty}` — strictly more hostile than any one LLM run, and deterministic. `test_total_spend_never_exceeds_mandate_across_many_hostile_requests` throws every product at every quantity against one mandate and checks total money moved stays under the cap.

That split is more or less the whole point of the project. The guardrail can't depend on the agent staying uncompromised, so it shouldn't be tested through the agent either.

## What I haven't verified

Saying this plainly rather than letting someone find it:

The agent's LLM loop has never actually run. I built this without an Anthropic API key. Its tools, argument validation, and guardrail integration are all tested and working against a live server, but the `client.messages.create` call itself is written from the SDK docs and unexercised. The property it's meant to illustrate is proven deterministically in `tests/test_compromised_agent.py`, so this is a demo weakness rather than a correctness one — but it's untested code and I'd rather say so.

Scope is deliberately small: five products, one merchant, single-use mandates, one user. This is a reference implementation of the enforcement layer, not a product.

Docker is verified. `docker compose up --build` builds clean and serves real traffic; the full demo runs against the container, not just my local venv.

## With more time

- Actual AP2/ACP schema compliance rather than a mandate format modeled on the pattern
- Multi-use mandates with a running spend total, instead of single-use
- Per-merchant catalogs rather than one seeded merchant
