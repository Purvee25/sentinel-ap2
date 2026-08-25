import os
import tempfile

import pytest


@pytest.fixture(autouse=True, scope="session")
def _isolated_data_dir():
    """Point the app at a throwaway data dir so tests never touch dev data
    and always start from a clean DB + fresh signing keypair."""
    tmp = tempfile.mkdtemp(prefix="sentinel-test-")
    os.environ["SENTINEL_DB_PATH"] = os.path.join(tmp, "test.db")
    os.environ["SENTINEL_KEY_DIR"] = os.path.join(tmp, "keys")
    yield


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
