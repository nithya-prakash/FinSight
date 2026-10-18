import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

from finsight.agents.text_to_sql.agent import BIAgent
from finsight.agents.text_to_sql.executor import ExecConfig
from finsight.db import migrate
from finsight.events import make_event
from finsight.generator import GeneratorConfig, generate
from finsight.generator.seed import seed
from finsight.pipeline.processor import Meta, process_event

DBT_DIR = Path(__file__).resolve().parents[2] / "warehouse" / "dbt"
DB = "finsight_bi_test"


@pytest.fixture(scope="session")
def bi_env():
    url = os.environ.get("DATABASE_URL")
    if not url or not shutil.which("dbt"):
        pytest.skip("needs DATABASE_URL and dbt")
    u = urlparse(url)
    base = url.rpartition("/")[0]
    with psycopg.connect(f"{base}/postgres", autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        admin.execute(f'CREATE DATABASE "{DB}"')
    owner = f"{base}/{DB}"
    ds = generate(GeneratorConfig(seed=7, n_customers=150, days=21, fraud_prevalence=0.03))
    with psycopg.connect(owner) as c:
        migrate(c)
        seed(c, ds, entities_only=True)
        c.commit()
        for t in ds.transactions:
            process_event(c, make_event(t).model_dump_json().encode(), Meta("t", 0, 0))
        ids = [r[0] for r in c.execute("SELECT alert_id FROM core.fraud_alerts ORDER BY alert_id LIMIT 3")]
        c.execute("INSERT INTO core.investigation_cases (alert_id, status, opened_at, closed_at) VALUES "
                  "(%s,'closed_fraud','2026-02-01 10:00+00','2026-02-01 14:00+00'),"
                  "(%s,'closed_legit','2026-02-01 10:00+00','2026-02-01 20:00+00'),(%s,'open','2026-02-01 10:00+00',NULL)", ids)
        c.commit()
    env = {**os.environ, "DBT_PG_HOST": u.hostname, "DBT_PG_PORT": str(u.port or 5432), "DBT_PG_USER": u.username,
           "DBT_PG_PASSWORD": u.password, "DBT_PG_DBNAME": DB}
    r = subprocess.run(["dbt", "build", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR),
                        "--target-path", "/tmp/dbt_target_bi", "--log-path", "/tmp/dbt_logs_bi"],
                       env=env, capture_output=True, text=True, timeout=400)
    assert r.returncode == 0, r.stdout[-2000:]
    bi = f"postgresql://finsight_bi:finsight_bi_dev_password@{u.hostname}:{u.port or 5432}/{DB}"
    return {"ds": ds, "owner": owner, "bi": bi}


@pytest.fixture()
def agent(bi_env):
    return BIAgent(bi_env["bi"], None, ExecConfig(row_limit=100), bi_env["owner"])


class FakeLLM:
    """Scripted stand-in used ONLY to test agent orchestration. It is not Claude and proves nothing about SQL quality."""
    model = "fake-scripted-llm"

    def __init__(self, *outputs: dict):
        self.outputs, self.calls = list(outputs), []

    def generate(self, system, user, tool=None):
        from finsight.agents.text_to_sql.llm import LLMResult
        self.calls.append(user)
        return LLMResult(self.outputs.pop(0), 100, 20, self.model)
