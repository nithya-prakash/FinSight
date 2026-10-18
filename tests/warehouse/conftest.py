import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

from finsight.events import make_event
from finsight.generator.seed import seed
from finsight.pipeline.processor import Meta, process_event

DBT_DIR = Path(__file__).resolve().parents[2] / "warehouse" / "dbt"


def dbt_env(test_db: str) -> dict:
    u = urlparse(os.environ["DATABASE_URL"])
    return {**os.environ, "DBT_PG_HOST": u.hostname, "DBT_PG_PORT": str(u.port or 5432), "DBT_PG_USER": u.username,
            "DBT_PG_PASSWORD": u.password, "DBT_PG_DBNAME": test_db,
            "DATABASE_URL": os.environ["DATABASE_URL"].rpartition("/")[0] + "/" + test_db, "DBT_PROJECT_DIR": str(DBT_DIR), "DAGSTER_DBT_PARSE_PROJECT_ON_LOAD": "1",
            "DAGSTER_HOME": os.environ.get("DAGSTER_TEST_HOME", "/tmp/dagster_test_home")}


def run_dbt(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["dbt", *args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
                           "--target-path", "/tmp/dbt_target", "--log-path", "/tmp/dbt_logs"],
                          env=env, capture_output=True, text=True, timeout=300)


@pytest.fixture()
def streamed(conn, ds):
    """Test DB with all entities, labels and every transaction streamed through the real processor,
    plus two investigation cases on known alerts."""
    if not shutil.which("dbt"):
        pytest.skip("dbt not installed")
    seed(conn, ds, entities_only=True)
    conn.commit()
    for t in ds.transactions:
        process_event(conn, make_event(t).model_dump_json().encode(), Meta("t", 0, 0))
    ids = [r[0] for r in conn.execute("SELECT alert_id FROM core.fraud_alerts ORDER BY alert_id LIMIT 2")]
    conn.execute("INSERT INTO core.investigation_cases (alert_id, status, opened_at, closed_at) VALUES "
                 "(%s,'closed_fraud','2026-02-01 10:00+00','2026-02-01 14:00+00'), (%s,'open','2026-02-01 10:00+00',NULL)",
                 ids)
    conn.commit()
    dbname = os.environ["DATABASE_URL"].rpartition("/")[2] + "_test"
    return ds, dbt_env(dbname)
