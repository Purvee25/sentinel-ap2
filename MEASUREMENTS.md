# Measured, not asserted

`tests/` proves nine named attacks and six compromised-agent scenarios fail
correctly. This is the same claim under load: thousands of randomly generated
hostile purchase requests fired at the guardrail, checking one thing after
every single one — did any mandate's cap get exceeded.

Reproduce with:

```bash
python scripts/fuzz.py --attempts 50000
```

Runs directly against `guardrail.process_purchase` (no server, no network),
against an isolated throwaway database — takes under two minutes for 50,000
attempts on a laptop.

## Latest run — 50,000 attempts

```
accepted     1,932
rejected    48,068
failed           0

attempted   ₹54,607,508,363.00   (~₹546 crore in intended spend, across all attempts)
blocked     ₹54,604,171,954.00
accepted    ₹      3,336,409.00   (~₹33 lakh — the legitimate purchases)

rejections by guardrail
  merchant mismatch      20,838
  exceeds mandate cap    11,787
  price mismatch          7,295
  mandate not found        5,003
  already used             3,145

CAP INVARIANT: HELD — 0 violations across 1,932 mandates that saw a charge
```

Figures match the committed [`fuzz_report.json`](fuzz_report.json) exactly. Re-running the command generates a fresh random run with different (but structurally similar) numbers — the seed isn't fixed on purpose, so each run is a genuinely new search rather than a replay. Paise values are large because random quantities range up to several thousand units per attempt; see "What attempted means" below.

## What "attempted" means here

Each request's intent is computed as `catalog_price × requested_qty`,
independent of what the guardrail reports back — a rejected request still
had an intended amount, it just never became money. Random quantities go up
to several thousand units, which is why the attempted total (~₹540 crore
across 50k attempts) dwarfs the charged total (~₹34 lakh): the fuzzer is not
trying to look realistic, it's trying to find a request that breaks the cap.

## Why this number and not a bigger one

50,000 was picked because it finishes in under two minutes and already shows
zero violations with wide margin — the rejection categories are each in the
thousands, meaning every guardrail check gets exercised heavily, not just
technically covered. `--attempts 500000` runs the same way if a larger number
is wanted; the invariant either holds or it doesn't; there is no reason to
believe the result changes with more attempts unless the underlying logic
does.

## What this does and doesn't prove

**Does prove:** for the specific attack surface this fuzzer generates — wrong
merchant, wrong claimed price, oversized quantity, forged or reused mandate
ids, all in combination — the guardrail's cap invariant holds under volume,
not just under the nine hand-written cases.

**Doesn't prove:** exhaustive coverage. The fuzzer doesn't attempt signature
tampering (that's `test_tampered_mandate_signature_is_rejected`, since
forging a *valid* signature isn't something random generation can stumble
into) or timing/concurrency attacks (out of scope — see NOTES.md). It's a
stress test of the checks that exist, not a search for checks that are
missing.
