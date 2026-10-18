"""Dagster orchestration for the FinSight warehouse.

Ownership contract: the streaming consumer is the ONLY writer of core.* (raw layer). Dagster/dbt only
read it and build stg/int/marts. `raw_ingested_events` is a gate asset that verifies ingestion has
produced data before the dbt models are (re)built.
"""
import os
import subprocess
from pathlib import Path

import psycopg
from dagster import (
    AssetExecutionContext,
    AssetKey,
    AssetSelection,
    AssetSpec,
    DefaultScheduleStatus,
    Definitions,
    Failure,
    MaterializeResult,
    RetryPolicy,
    ScheduleDefinition,
    define_asset_job,
    multi_asset,
)
from dagster_dbt import DbtCliResource, DbtProject, dbt_assets

from finsight.db import one

DBT_DIR = Path(os.environ.get("DBT_PROJECT_DIR", Path(__file__).resolve().parents[2] / "warehouse" / "dbt"))
RETRY = RetryPolicy(max_retries=3, delay=10)


def _database_url() -> str:
    return os.environ["DATABASE_URL"]


RAW_TABLES = ["transactions", "customers", "accounts", "merchants", "merchant_categories",
              "model_predictions", "fraud_alerts", "investigation_cases", "processed_events"]
RAW_SPECS = [AssetSpec(key=AssetKey(["core", t]), group_name="raw") for t in RAW_TABLES] + [
    AssetSpec(key=AssetKey(["eval", "fraud_labels"]), group_name="raw")]


@multi_asset(specs=RAW_SPECS, name="raw_ingested_events", retry_policy=RETRY,
             description="Gate: verifies the streaming consumer has persisted events (owned by fraud-consumer).")
def raw_ingested_events(context: AssetExecutionContext):
    with psycopg.connect(_database_url()) as conn:
        n, last = one(conn.execute("SELECT count(*), max(processed_at) FROM core.processed_events"))
        (dead,) = one(conn.execute("SELECT count(*) FROM core.dead_letter_events"))
    if n == 0:
        raise Failure("No events have been ingested yet (core.processed_events is empty)")
    context.log.info(f"{n} events ingested, {dead} dead letters")
    for spec in RAW_SPECS:
        yield MaterializeResult(asset_key=spec.key, metadata={
            "processed_events": n, "dead_letters": dead, "last_processed_at": str(last)})


dbt_project = DbtProject(project_dir=DBT_DIR)
if not dbt_project.manifest_path.exists() or os.environ.get("DAGSTER_DBT_PARSE_PROJECT_ON_LOAD"):
    # (re)generate target/manifest.json so the asset graph always reflects the current models
    subprocess.run(["dbt", "parse", "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)],
                   check=True, capture_output=True)


@dbt_assets(manifest=dbt_project.manifest_path, retry_policy=RETRY)
def finsight_dbt_models(context: AssetExecutionContext, dbt: DbtCliResource):
    # `build` = run + test, so data-quality failures surface as failed asset checks / run failure.
    yield from dbt.cli(["build"], context=context).stream()


warehouse_job = define_asset_job("warehouse_build", selection=AssetSelection.all())
daily_schedule = ScheduleDefinition(job=warehouse_job, cron_schedule="0 2 * * *",
                                    default_status=DefaultScheduleStatus.RUNNING)

defs = Definitions(
    assets=[raw_ingested_events, finsight_dbt_models],
    jobs=[warehouse_job],
    schedules=[daily_schedule],
    resources={"dbt": DbtCliResource(project_dir=dbt_project)},
)
