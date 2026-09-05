import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.catalog import SEED_MERCHANT_ID, seed_catalog
from app.database import Base, SessionLocal, engine
from app.db_models import Mandate, Product
from app.guardrail import process_purchase
from app.mandate import create_signed_mandate
from app.routers import audit, mandates, purchase

STATIC_DIR = Path(__file__).parent / "static"
FUZZ_REPORT_PATH = Path(__file__).parent.parent / "fuzz_report.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_catalog(db)
    finally:
        db.close()
    yield


app = FastAPI(
    title="Sentinel-AP2",
    description="Deterministic guardrail middleware for autonomous agent payments on Razorpay test-mode.",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers at both /api/v1 (new) and / (legacy) for backward compatibility
app.include_router(mandates.router, prefix="/api/v1")
app.include_router(purchase.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")

# Legacy routes for existing frontend
app.include_router(mandates.router)
app.include_router(purchase.router)
app.include_router(audit.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/fuzz-report", include_in_schema=False)
def fuzz_report():
    """Serves the committed output of scripts/fuzz.py so the dashboard's
    scale claim is read from the actual artifact, not hardcoded in the page."""
    if not FUZZ_REPORT_PATH.exists():
        raise HTTPException(status_code=404, detail="no fuzz report committed yet")
    return json.loads(FUZZ_REPORT_PATH.read_text())


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


SIMULATION_CAP_PAISE = 150_000  # ₹1,500, matches the README walkthrough
SIMULATION_TTL_SECONDS = 3600


def _issue_mandate(db, merchant_id: str, cap_paise: int) -> Mandate:
    signed = create_signed_mandate(merchant_id, cap_paise, SIMULATION_TTL_SECONDS)
    mandate = Mandate(
        id=signed["id"], merchant_id=signed["merchant_id"],
        max_amount_paise=signed["max_amount_paise"], nonce=signed["nonce"],
        issued_at=signed["issued_at"], expires_at=signed["expires_at"],
        signature_b64=signed["signature_b64"], status="active",
    )
    db.add(mandate)
    db.commit()
    return mandate


@app.post("/api/v1/simulate/run", include_in_schema=False)
def run_adversarial_simulation():
    """Runs the README walkthrough against fresh mandates: one legitimate
    purchase, then four attacks that each die at a different guardrail check."""
    db = SessionLocal()
    try:
        products = {p.name: p for p in db.query(Product).all()}
        mouse, earbuds = products["Wireless Mouse"], products["Noise Cancelling Earbuds"]
        merchant = SEED_MERCHANT_ID

        legit = _issue_mandate(db, merchant, SIMULATION_CAP_PAISE)
        scenarios = [
            ("Legitimate purchase", legit.id, merchant, mouse.id, 1, None),
            ("Replay attack", legit.id, merchant, mouse.id, 1, None),
            ("Over-budget (injected product)", _issue_mandate(db, merchant, SIMULATION_CAP_PAISE).id,
             merchant, earbuds.id, 50, None),
            ("Merchant swap", _issue_mandate(db, merchant, SIMULATION_CAP_PAISE).id,
             "attacker_merchant", mouse.id, 1, None),
            ("Price tampering", _issue_mandate(db, merchant, SIMULATION_CAP_PAISE).id,
             merchant, mouse.id, 1, 100),
        ]

        results = []
        for label, mandate_id, req_merchant, product_id, qty, claimed in scenarios:
            started = time.perf_counter()
            res = process_purchase(
                db=db, mandate_id=mandate_id, requested_merchant_id=req_merchant,
                product_id=product_id, qty=qty, client_claimed_price_paise=claimed,
            )
            results.append({
                "scenario": label,
                "status": res.status,
                "reason": res.reason,
                "computed_total_paise": res.computed_total_paise,
                "transaction_id": res.transaction_id,
                "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            })
        return {"simulations": results, "success": True}
    finally:
        db.close()


@app.get("/", include_in_schema=False)
def dashboard():
    """Operator dashboard: issue mandates, fire agent purchases and attacks,
    and watch the guardrail's verdicts land in the audit trail live."""
    return FileResponse(STATIC_DIR / "index.html")
