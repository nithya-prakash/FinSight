import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from finsight.db import migrate
from finsight.generator import GeneratorConfig, generate


@pytest.fixture(scope="session")
def ds():
    return generate(GeneratorConfig(seed=7, n_customers=150, days=21, fraud_prevalence=0.03))


@pytest.fixture()
def conn():
    """Uses a dedicated <db>_test database so the dev data is never dropped."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")
    base, _, name = url.rpartition("/")
    test_name = f"{name}_test"
    with psycopg.connect(f"{base}/postgres", autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (test_name,)).fetchone():
            admin.execute(f'CREATE DATABASE "{test_name}"')
    with psycopg.connect(f"{base}/{test_name}") as c:
        c.execute("DROP SCHEMA IF EXISTS core CASCADE; DROP SCHEMA IF EXISTS eval CASCADE; "
                  "DROP TABLE IF EXISTS public.schema_migrations")
        c.commit()
        migrate(c)
        yield c


# ---- API authentication for tests: every TestClient acts as the analyst unless it passes headers explicitly ----
ANALYST_KEY, VIEWER_KEY = "test-analyst-key", "test-viewer-key"
_orig_init = TestClient.__init__


def _init(self, *a, **k):
    k.setdefault("headers", {"x-api-key": ANALYST_KEY})
    _orig_init(self, *a, **k)


TestClient.__init__ = _init  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _auth_env(monkeypatch):
    monkeypatch.setenv("AUTH_KEYS", f"{VIEWER_KEY}:viewer:viewer-test,{ANALYST_KEY}:analyst:analyst-test")
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
