import importlib.util
import os
import shutil
from pathlib import Path

import pytest

from .conftest import DBT_DIR  # noqa: F401  (fixture side effects)

pytestmark = pytest.mark.integration
DEFS = Path(__file__).resolve().parents[2] / "orchestration" / "dagster" / "definitions.py"


def load_defs(env: dict):
    os.environ.update({k: env[k] for k in env if k.startswith(("DBT_", "DAGSTER_")) or k == "DATABASE_URL"})
    spec = importlib.util.spec_from_file_location("finsight_dagster_defs", DEFS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_definitions_graph_job_and_schedule(streamed):
    _, env = streamed
    mod = load_defs(env)
    keys = {k.to_user_string() for k in mod.defs.resolve_asset_graph().get_all_asset_keys()}
    assert "core/transactions" in keys and "marts/fact_transactions" in keys and "marts/metrics_daily" in keys
    job = mod.defs.resolve_job_def("warehouse_build")
    assert job.name == "warehouse_build"
    sched = mod.defs.get_schedule_def(mod.daily_schedule.name)
    assert sched.cron_schedule == "0 2 * * *"
    # explicit dependency: dbt staging models depend on the raw ingestion gate
    graph = mod.defs.resolve_asset_graph()
    from dagster import AssetKey
    assert AssetKey(["core", "transactions"]) in graph.get(AssetKey(["stg", "stg_transactions"])).parent_keys


def test_pipeline_run_executes_and_reports(streamed, conn):
    from dagster import DagsterInstance, materialize
    from dagster_dbt import DbtCliResource
    _, env = streamed
    mod = load_defs(env)
    shutil.rmtree(env["DAGSTER_HOME"], ignore_errors=True)
    Path(env["DAGSTER_HOME"]).mkdir(parents=True)
    res = materialize([mod.raw_ingested_events, mod.finsight_dbt_models],
                      resources={"dbt": DbtCliResource(project_dir=str(DBT_DIR), profiles_dir=str(DBT_DIR))},
                      instance=DagsterInstance.ephemeral(), raise_on_error=False)
    assert res.success
    mats = [e.asset_key.to_user_string() for e in res.get_asset_materialization_events()]
    assert "core/transactions" in mats and "marts/fact_transactions" in mats
    conn.rollback()
    assert conn.execute("select count(*) from marts.fact_transactions").fetchone()[0] > 0


def test_pipeline_fails_when_nothing_ingested(conn, ds):
    from dagster import materialize
    from dagster._core.errors import DagsterExecutionStepExecutionError  # noqa: F401

    from .conftest import dbt_env
    env = dbt_env(os.environ["DATABASE_URL"].rpartition("/")[2] + "_test")
    mod = load_defs(env)
    res = materialize([mod.raw_ingested_events], raise_on_error=False)
    assert not res.success  # empty ingestion ledger => explicit failure, not a silent empty build
