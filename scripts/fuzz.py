"""Randomized adversarial stress test for the guardrail engine.

The unit test suite covers specific, named attacks. This covers the general
case: thousands of randomly-generated hostile purchase requests fired at
freshly-issued mandates, checking one invariant after every single one —
that a mandate's cap was never exceeded.

Runs directly against guardrail.process_purchase (no HTTP, no server) so it
can push tens of thousands of attempts in seconds. Talks to an isolated
throwaway database, same as the test suite — never touches dev data.

    python scripts/fuzz.py                  # 5,000 attempts
    python scripts/fuzz.py --attempts 50000  # more
"""

import argparse
import json
import os
import random
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _isolate_environment() -> None:
    tmp = tempfile.mkdtemp(prefix="sentinel-fuzz-")
    os.environ["SENTINEL_DB_PATH"] = os.path.join(tmp, "fuzz.db")
    os.environ["SENTINEL_KEY_DIR"] = os.path.join(tmp, "keys")
    os.environ["RAZORPAY_KEY_ID"] = ""
    os.environ["RAZORPAY_KEY_SECRET"] = ""


_isolate_environment()

from app.catalog import SEED_MERCHANT_ID, seed_catalog  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.db_models import Mandate, Product  # noqa: E402
from app.guardrail import process_purchase  # noqa: E402
from app.mandate import create_signed_mandate  # noqa: E402

REASON_CATEGORIES = [
    "mandate not found",
    "already used",
    "invalid mandate signature",
    "mandate expired",
    "merchant mismatch",
    "product not found",
    "invalid quantity",
    "price mismatch",
    "exceeds mandate cap",
]


def categorize(reason: str) -> str:
    for cat in REASON_CATEGORIES:
        if cat in reason:
            return cat
    return "other"


def issue_mandate(db, merchant_id: str, cap_paise: int) -> Mandate:
    signed = create_signed_mandate(merchant_id=merchant_id, max_amount_paise=cap_paise, ttl_seconds=3600)
    row = Mandate(
        id=signed["id"], merchant_id=signed["merchant_id"], max_amount_paise=signed["max_amount_paise"],
        nonce=signed["nonce"], issued_at=signed["issued_at"], expires_at=signed["expires_at"],
        signature_b64=signed["signature_b64"], status="active",
    )
    db.add(row)
    db.commit()
    return row


def random_attempt(db, products: list[Product], mandate: Mandate, rng: random.Random):
    """Builds and fires one randomized, mostly-hostile purchase request
    against `mandate`. Returns (PurchaseResult, intent_paise)."""
    product = rng.choice(products)

    # merchant: usually correct, sometimes substituted
    merchant_id = mandate.merchant_id if rng.random() < 0.5 else f"attacker_{rng.randrange(9999)}"

    # quantity: mix of plausible and wildly excessive
    qty = rng.choice([1, 1, 2, 5, 10, 50, 100, 1000, rng.randrange(1, 5000)])

    # claimed price: usually absent (honest), sometimes wrong by a random factor
    if rng.random() < 0.35:
        claimed = max(1, int(product.price_paise * rng.choice([0, 0.01, 0.5, 2, 10])))
    else:
        claimed = None

    # mandate id: usually the real one (to test cap/replay), sometimes forged
    mandate_id = mandate.id if rng.random() < 0.9 else str(uuid.uuid4())

    result = process_purchase(
        db=db, mandate_id=mandate_id, requested_merchant_id=merchant_id,
        product_id=product.id, qty=qty, client_claimed_price_paise=claimed,
    )
    # What the agent was actually trying to spend, independent of whether the
    # guardrail computed (and reported) a total — a rejection always reports
    # 0 charged, which is correct for money moved but useless for measuring
    # what was *attempted*.
    intent_paise = product.price_paise * qty
    return result, intent_paise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempts", type=int, default=5000)
    parser.add_argument("--seed", type=int, default=None, help="omit for a fresh random run each time")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    seed_catalog(db)
    products = db.query(Product).all()

    results = Counter()
    reason_counts = Counter()
    attempted_paise = 0
    accepted_paise = 0
    per_mandate_charged: dict[str, int] = {}
    cap_violations = []

    mandate = issue_mandate(db, SEED_MERCHANT_ID, cap_paise=rng.randrange(500, 500_000))
    attempts_on_mandate = 0

    for i in range(args.attempts):
        # Rotate to a fresh mandate periodically so both "many attacks on one
        # mandate" and "many mandates" get exercised.
        attempts_on_mandate += 1
        if attempts_on_mandate > rng.randrange(1, 8):
            mandate = issue_mandate(db, SEED_MERCHANT_ID, cap_paise=rng.randrange(500, 500_000))
            attempts_on_mandate = 0

        result, intent_paise = random_attempt(db, products, mandate, rng)
        results[result.status] += 1
        if result.status != "accepted":
            reason_counts[categorize(result.reason)] += 1

        attempted_paise += intent_paise
        if result.status == "accepted":
            accepted_paise += result.computed_total_paise
            spent_so_far = per_mandate_charged.get(mandate.id, 0) + result.computed_total_paise
            per_mandate_charged[mandate.id] = spent_so_far
            if spent_so_far > mandate.max_amount_paise:
                cap_violations.append({
                    "mandate_id": mandate.id, "cap": mandate.max_amount_paise, "charged": spent_so_far,
                })

    report = {
        "attempts": args.attempts,
        "accepted": results["accepted"],
        "rejected": results["rejected"],
        "failed": results.get("failed", 0),
        "attempted_paise": attempted_paise,
        "accepted_paise": accepted_paise,
        "blocked_paise": attempted_paise - accepted_paise,
        "rejection_reasons": dict(reason_counts),
        "cap_violations": cap_violations,
    }

    print(f"\n{'=' * 60}\nFUZZ RUN: {args.attempts} randomized hostile purchase attempts\n{'=' * 60}\n")
    print(f"  accepted  {report['accepted']:>7}")
    print(f"  rejected  {report['rejected']:>7}")
    print(f"  failed    {report['failed']:>7}\n")
    print(f"  attempted  ₹{attempted_paise / 100:>14,.2f}")
    print(f"  blocked    ₹{report['blocked_paise'] / 100:>14,.2f}")
    print(f"  accepted   ₹{accepted_paise / 100:>14,.2f}\n")
    print("  rejections by guardrail:")
    for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {reason:<28} {count:>6}")

    print(f"\n  CAP INVARIANT: {'HELD' if not cap_violations else 'VIOLATED'} "
          f"({len(cap_violations)} violation(s) across {len(per_mandate_charged)} mandates that saw a charge)")

    out_path = Path(__file__).resolve().parent.parent / "fuzz_report.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\n  report written to {out_path}")

    db.close()

    if cap_violations:
        print("\nFAILED: cap invariant violated. See fuzz_report.json.")
        sys.exit(1)


if __name__ == "__main__":
    main()
