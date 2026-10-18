import os
from urllib.parse import urlparse

import psycopg
import pytest

from finsight.agents.investigator.service import make_service
from finsight.config import Settings
from finsight.db import migrate
from finsight.events import make_event
from finsight.generator import GeneratorConfig, generate
from finsight.generator.seed import seed
from finsight.pipeline.processor import Meta, process_event

DB = "finsight_inv_test"


@pytest.fixture(scope="module")
def env():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("DATABASE_URL not set")
    u, base = urlparse(url), url.rpartition("/")[0]
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
        c.commit()
        alerts = {}
        for scen in ("velocity_burst", "mule_ring", "impossible_travel"):
            alerts[scen] = c.execute(
                "SELECT fa.alert_id FROM core.fraud_alerts fa JOIN eval.fraud_labels l USING (transaction_id) "
                "WHERE l.scenario=%s AND l.is_fraud ORDER BY fa.alert_id DESC LIMIT 1", (scen,)).fetchone()
        # an alert on an account with almost no history (earliest alert)
        alerts["thin"] = c.execute("SELECT alert_id FROM core.fraud_alerts ORDER BY alert_id LIMIT 1").fetchone()
        alerts = {k: v[0] for k, v in alerts.items() if v}
    inv = f"postgresql://finsight_investigator:finsight_inv_dev_password@{u.hostname}:{u.port or 5432}/{DB}"
    return {"owner": owner, "inv": inv, "alerts": alerts, "ds": ds}


def settings_for(env) -> Settings:
    return Settings(database_url=env["owner"], investigator_database_url=env["inv"])


@pytest.fixture()
def svc_factory(env):
    def make(llm=None):
        return make_service(settings_for(env), llm)
    return make


def fresh_alert(env, n: int = 0) -> int:
    """An alert without an active investigation run (starts are idempotent per alert)."""
    with psycopg.connect(env["owner"]) as c:
        rows = c.execute("SELECT alert_id FROM core.fraud_alerts a WHERE NOT EXISTS (SELECT 1 FROM core.investigation_runs r "
                         "WHERE r.alert_id=a.alert_id AND r.state IN ('running','pending_review')) ORDER BY alert_id DESC").fetchall()
    return rows[n][0]
