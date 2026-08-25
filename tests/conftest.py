import os
import tempfile

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_data_dir():
    """Throwaway data dir, and force mock payments.

    Without the mock override, anyone with real keys in .env would have the
    suite creating live orders on every accepted purchase — junk in their
    dashboard, and tests that need the network. These tests are about the
    guardrail's decisions, which don't change between modes.
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
