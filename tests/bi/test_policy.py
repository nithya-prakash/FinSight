from pathlib import Path

import pytest
import yaml

from finsight.agents.text_to_sql.policy import PolicyViolation, validate_sql

ATTACKS = yaml.safe_load((Path(__file__).parents[2] / "evals/text_to_sql/attacks.yaml").read_text())["attacks"]


@pytest.mark.parametrize("a", ATTACKS, ids=[a["id"] for a in ATTACKS])
def test_attack_rejected(a):
    with pytest.raises(PolicyViolation):
        validate_sql(a["sql"])


VALID = [
    "SELECT channel, COUNT(*) FROM marts.fact_transactions GROUP BY channel",
    "SELECT d.date_day, m.txn_count FROM marts.metrics_daily m JOIN marts.dim_date d USING (date_key) ORDER BY 1",
    "WITH a AS (SELECT account_id, COUNT(*) c FROM marts.fact_fraud_alerts GROUP BY 1) SELECT * FROM a ORDER BY c DESC",
    "SELECT date_trunc('week', event_time) w, SUM(amount) FROM marts.fact_transactions GROUP BY 1",
    "SELECT r, COUNT(*) FROM marts.fact_fraud_predictions p CROSS JOIN LATERAL UNNEST(p.rules_triggered) AS r GROUP BY r",
    "SELECT COUNT(*) FILTER (WHERE is_fraud_label) * 1.0 / NULLIF(COUNT(*) FILTER (WHERE has_label), 0) FROM marts.fact_transactions",
    "SELECT category_code FROM marts.dim_merchant UNION SELECT category_code FROM marts.metrics_by_category",
]


@pytest.mark.parametrize("q", VALID)
def test_valid_queries_pass(q):
    v = validate_sql(q, row_limit=50)
    assert "LIMIT" in v.sql.upper()


def test_limit_is_enforced_and_clamped():
    assert validate_sql("SELECT * FROM marts.dim_channel", 50).sql.endswith("LIMIT 50")
    assert validate_sql("SELECT * FROM marts.dim_channel LIMIT 100000", 50).sql.endswith("LIMIT 50")
    assert validate_sql("SELECT * FROM marts.dim_channel LIMIT 5", 50).sql.endswith("LIMIT 5")


def test_sensitive_projection_detection():
    v = validate_sql("SELECT account_id AS acct, COUNT(*) AS n FROM marts.fact_fraud_alerts GROUP BY 1", 50)
    assert v.sensitive_projections == [0]          # bare id masked even when aliased; COUNT not masked
    v2 = validate_sql("SELECT COUNT(DISTINCT customer_id) AS n FROM marts.fact_transactions", 50)
    assert v2.sensitive_projections == []
