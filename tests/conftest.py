import os
import tempfile

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_data_dir():
    """Point the app at a throwaway data dir so tests never touch dev data
    and always start from a clean DB + fresh signing keypair.

    Also force mock payment mode. A developer with real credentials in .env
    would otherwise have the suite create live test-mode orders on every
    accepted purchase — filling their Razorpay dashboard with junk and making
    the tests depend on the network. Payment execution is not what these
    tests are about; the guardrail's decisions are.
    """
    tmp = tempfile.mkdtemp(prefix="sentinel-test-")
    os.environ["SENTINEL_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["SENTINEL_KEY_DIR"] = os.path.join(tmp, "keys")
    # Set empty rather than deleting: app.config calls load_dotenv(), which
    # would repopulate absent keys from .env but leaves existing ones alone.
    os.environ["RAZORPAY_KEY_ID"] = ""
    os.environ["RAZORPAY_KEY_SECRET"] = ""
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
