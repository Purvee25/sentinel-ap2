import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.catalog import seed_catalog
from app.database import Base, SessionLocal, engine
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


@app.post("/api/v1/simulate/run", include_in_schema=False)
def run_adversarial_simulation():
    """Execute a batch of adversarial scenarios and return results."""
    from app.guardrail import process_purchase
    from app.mandate import Mandate
    from app.keys import load_keypair
    import time

    db = SessionLocal()
    pubkey, privkey = load_keypair()
    results = []

    try:
        # Scenario 1: Legitimate spend
        mandate = Mandate.create(
            db=db, merchant_id="test_merchant", max_amount_paise=10000, ttl_seconds=3600, pubkey=pubkey, privkey=privkey
        )
        res = process_purchase(
            db=db, mandate_id=mandate.id, merchant_id="test_merchant",
            product_id="wireless_mouse", qty=1
        )
        results.append({
            "scenario": "legitimate_spend",
            "status": res.status,
            "reason": res.reason,
            "timestamp": int(time.time() * 1000)
        })

        # Scenario 2: Over-budget attack
        mandate2 = Mandate.create(
            db=db, merchant_id="test_merchant", max_amount_paise=500, ttl_seconds=3600, pubkey=pubkey, privkey=privkey
        )
        res = process_purchase(
            db=db, mandate_id=mandate2.id, merchant_id="test_merchant",
            product_id="laptop_stand", qty=10
        )
        results.append({
            "scenario": "over_budget",
            "status": res.status,
            "reason": res.reason,
            "timestamp": int(time.time() * 1000)
        })

        return {"simulations": results, "success": True}
    except Exception as e:
        return {"error": str(e), "success": False}
    finally:
        db.close()


@app.get("/", include_in_schema=False)
def dashboard():
    """Operator dashboard: issue mandates, fire agent purchases and attacks,
    and watch the guardrail's verdicts land in the audit trail live."""
    return FileResponse(STATIC_DIR / "index.html")
