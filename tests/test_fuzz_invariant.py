"""A fast slice of the fuzzer, wired into the regular test run.

scripts/fuzz.py is the tool for a real stress run (thousands of attempts,
run by hand — see MEASUREMENTS.md). This is the same generator at a size
small enough to run on every `pytest`, so the invariant it checks can't
silently rot between real fuzz runs.
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from fuzz import issue_mandate, random_attempt  # noqa: E402

from app.catalog import SEED_MERCHANT_ID  # noqa: E402
from app.db_models import Product  # noqa: E402


def test_fuzz_invariant_holds_at_small_scale(client, db_session):
    """`client` seeds the catalog via the app; reuse that session directly
    so this exercises the exact same guardrail code path as a real fuzz run."""
    rng = random.Random(1234)
    products = db_session.query(Product).all()
    assert products, "catalog must be seeded before fuzzing"

    violations = []
    mandate = issue_mandate(db_session, SEED_MERCHANT_ID, cap_paise=rng.randrange(500, 500_000))
    spent = {}

    for i in range(300):
        if i % rng.randrange(1, 8) == 0:
            mandate = issue_mandate(db_session, SEED_MERCHANT_ID, cap_paise=rng.randrange(500, 500_000))

        result, _ = random_attempt(db_session, products, mandate, rng)
        if result.status == "accepted":
            spent[mandate.id] = spent.get(mandate.id, 0) + result.computed_total_paise
            if spent[mandate.id] > mandate.max_amount_paise:
                violations.append((mandate.id, spent[mandate.id], mandate.max_amount_paise))

    assert violations == [], f"cap invariant violated: {violations}"
