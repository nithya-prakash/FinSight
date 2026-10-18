import pytest

from finsight.db import health, migrate
from finsight.generator.seed import seed

pytestmark = pytest.mark.integration


def test_migrations_idempotent_and_tamper_detected(conn, tmp_path):
    assert migrate(conn) == []  # fixture already applied everything
    assert health(conn)["ready"]
    (tmp_path / "0001_core_schema.sql").write_text("SELECT 1;")
    with pytest.raises(RuntimeError, match="modified"):
        migrate(conn, tmp_path)


def test_seed_roundtrip_and_constraints(conn, ds):
    info = seed(conn, ds)
    assert info["transactions"] == len(ds.transactions)
    n, = conn.execute("SELECT count(*) FROM core.transactions").fetchone()
    nl, = conn.execute("SELECT count(*) FROM eval.fraud_labels WHERE is_fraud").fetchone()
    assert n == len(ds.transactions) and nl == sum(l.is_fraud for l in ds.labels)
    with pytest.raises(RuntimeError, match="already seeded"):
        seed(conn, ds)
    seed(conn, ds, reset_first=True)  # reseed replaces


def test_db_rejects_invalid_rows(conn, ds):
    import psycopg
    seed(conn, ds)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE core.transactions SET amount = -1 WHERE transaction_id = 'txn_00000000'")
    conn.rollback()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        conn.execute("UPDATE core.transactions SET account_id = 'nope' WHERE transaction_id = 'txn_00000000'")


def test_labels_isolated_from_core_schema(conn):
    cols = {r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='core'")}
    assert not cols & {"is_fraud", "scenario", "scenario_instance_id"}


def test_seed_deterministic_in_db(conn, ds):
    seed(conn, ds)
    a = conn.execute("SELECT md5(string_agg(transaction_id||amount::text,',' ORDER BY transaction_id)) "
                     "FROM core.transactions").fetchone()
    seed(conn, ds, reset_first=True)
    b = conn.execute("SELECT md5(string_agg(transaction_id||amount::text,',' ORDER BY transaction_id)) "
                     "FROM core.transactions").fetchone()
    assert a == b
