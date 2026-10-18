from collections import Counter, defaultdict

import pytest

from .conftest import run_dbt

pytestmark = pytest.mark.integration


def q(conn, sql):
    rows = conn.execute(sql).fetchall()
    conn.rollback()  # never hold locks that would block dbt's DROP/CREATE
    return rows


def test_dbt_build_passes_all_models_and_tests(streamed):
    ds, env = streamed
    r = run_dbt(["build"], env)
    assert r.returncode == 0, r.stdout[-3000:]
    assert "ERROR" not in r.stdout.split("Done.")[0].replace("ERROR=0", "")


def test_marts_match_independent_python_calculation(streamed, conn):
    ds, env = streamed
    assert run_dbt(["build"], env).returncode == 0
    conn.rollback()
    txns = {t.transaction_id: t for t in ds.transactions}
    labels = {l.transaction_id: l.is_fraud for l in ds.labels}
    # --- row counts / dims
    assert q(conn, "select count(*) from marts.fact_transactions")[0][0] == len(ds.transactions)
    assert q(conn, "select count(*) from marts.dim_customer")[0][0] == len(ds.customers)
    assert q(conn, "select count(*) from marts.dim_merchant")[0][0] == len(ds.merchants)
    assert q(conn, "select count(*) from marts.dim_account")[0][0] == len(ds.accounts)
    # --- totals
    tot = q(conn, "select sum(txn_count), sum(txn_value), sum(fraud_txn_count), sum(fraud_value) from marts.metrics_daily")[0]
    assert tot[0] == len(ds.transactions)
    assert float(tot[1]) == pytest.approx(sum(float(t.amount) for t in ds.transactions))
    assert tot[2] == sum(labels.values())
    assert float(tot[3]) == pytest.approx(sum(float(txns[i].amount) for i, f in labels.items() if f))
    # --- confusion matrix vs alerts
    flagged = {r[0] for r in q(conn, "select transaction_id from core.fraud_alerts")}
    tp = len(flagged & {i for i, f in labels.items() if f})
    fp = len(flagged - {i for i, f in labels.items() if f})
    fn = sum(labels.values()) - tp
    got = q(conn, "select sum(true_positives), sum(false_positives), sum(false_negatives) from marts.metrics_daily")[0]
    assert tuple(map(int, got)) == (tp, fp, fn)
    # --- per-day fraud rate and flagged_rate (flagged != fraud rate)
    by_day = defaultdict(lambda: [0, 0, 0])
    for t in ds.transactions:
        d = by_day[t.event_time.date()]
        d[0] += 1
        d[1] += labels[t.transaction_id]
        d[2] += t.transaction_id in flagged
    for day, rate, frate in q(conn, "select d.date_day, m.fraud_rate, m.flagged_rate from marts.metrics_daily m "
                                    "join marts.dim_date d using (date_key)"):
        n, f, fl = by_day[day]
        assert float(rate) == pytest.approx(f / n) and float(frate) == pytest.approx(fl / n)
    # --- fraud by merchant category
    cat = {m.merchant_id: m.category_code for m in ds.merchants}
    exp = Counter(cat[t.merchant_id] for t in ds.transactions if t.merchant_id and labels[t.transaction_id])
    for code, n in q(conn, "select category_code, fraud_txn_count from marts.metrics_by_category"):
        assert n == exp.get(code, 0)
    # --- alert -> investigation conversion and resolution time (2 cases seeded by the fixture)
    a = q(conn, "select alert_count, investigated_count, avg_resolution_hours, confirmed_fraud_cases from marts.metrics_alerts")[0]
    assert a[0] == len(flagged) and a[1] == 2 and float(a[2]) == 4.0 and a[3] == 1
    # --- features: velocity includes only trailing window; history excludes the current row
    first = q(conn, "select prior_txn_count from marts.fact_transactions order by event_time, transaction_id limit 1")[0][0]
    assert first == 0
    burst = q(conn, "select max(txn_count_10m) from marts.fact_transactions")[0][0]
    assert burst >= 6


def test_dbt_build_is_idempotent(streamed, conn):
    ds, env = streamed
    sql = ("select md5(string_agg(transaction_id || amount::text || coalesce(risk_score::text,'') || txn_count_1h::text, ',' "
           "order by transaction_id)) from marts.fact_transactions")
    assert run_dbt(["build"], env).returncode == 0
    conn.rollback()
    a = q(conn, sql)
    assert run_dbt(["build"], env).returncode == 0
    conn.rollback()
    assert q(conn, sql) == a


def test_data_quality_test_catches_corruption(streamed, conn):
    ds, env = streamed
    assert run_dbt(["build"], env).returncode == 0
    # an alert whose prediction disappears violates the documented business rule
    conn.execute("DELETE FROM core.model_predictions WHERE transaction_id = (SELECT transaction_id FROM core.fraud_alerts LIMIT 1)")
    conn.commit()
    r = run_dbt(["test", "--select", "assert_every_alert_has_prediction"], env)
    assert r.returncode != 0 and "assert_every_alert_has_prediction" in r.stdout


def test_warehouse_has_no_customer_names_or_scenario_metadata(streamed, conn):
    ds, env = streamed
    assert run_dbt(["build"], env).returncode == 0
    cols = {(r[0], r[1]) for r in q(conn, "select table_schema, column_name from information_schema.columns "
                                         "where table_schema in ('stg','int','marts')")}
    assert not [c for c in cols if c[1] == "full_name"]
    assert not [c for c in cols if c[0] == "marts" and c[1] in ("scenario", "scenario_instance_id")]
