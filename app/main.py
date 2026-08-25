from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.catalog import seed_catalog
from app.database import Base, SessionLocal, engine
from app.routers import audit, mandates, purchase

STATIC_DIR = Path(__file__).parent / "static"


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

app.include_router(mandates.router)
app.include_router(purchase.router)
app.include_router(audit.router)


@app.get("/health")
def health():
    return {"status": "ok"}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def dashboard():
    """Operator dashboard: issue mandates, fire agent purchases and attacks,
    and watch the guardrail's verdicts land in the audit trail live."""
    return FileResponse(STATIC_DIR / "index.html")
