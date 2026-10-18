import psycopg

from ..db import one
from ..generator.models import Transaction
from .rules import Context, Hist

_AT = "(t.event_time, t.transaction_id) < (%(ts)s, %(id)s)"


def fetch_context(conn: psycopg.Connection, txn: Transaction) -> Context:
    p = {"ts": txn.event_time, "id": txn.transaction_id, "cust": txn.customer_id, "acc": txn.account_id}
    ctx = Context(txn=txn)
    if txn.merchant_id:
        row = conn.execute("SELECT m.category_code, c.risk_tier FROM core.merchants m JOIN "
                           "core.merchant_categories c USING (category_code) WHERE merchant_id=%s",
                           (txn.merchant_id,)).fetchone()
        if row:
            ctx.category, ctx.risk_tier = row
    if txn.device_id:
        d = conn.execute("SELECT customer_id, device_type FROM core.devices WHERE device_id=%s",
                         (txn.device_id,)).fetchone()
        ctx.device_known = d is not None and (d[0] == txn.customer_id or d[1] in ("pos_terminal", "atm"))
    ctx.recent = [Hist(r[0], r[1], r[2], r[3], float(r[4]), r[5], r[6]) for r in conn.execute(
        f"SELECT transaction_id, event_time, account_id, merchant_id, amount, lat, lon "
        f"FROM core.transactions t WHERE customer_id=%(cust)s AND {_AT} "
        f"AND event_time >= %(ts)s - interval '24 hours' ORDER BY event_time", p)]
    row = one(conn.execute(
        f"SELECT count(*), avg(amount), stddev_pop(amount) FROM core.transactions t "
        f"WHERE customer_id=%(cust)s AND {_AT}", p))
    ctx.prior_count = row[0]
    ctx.prior_mean = float(row[1]) if row[1] is not None else None
    ctx.prior_std = float(row[2]) if row[2] is not None else None
    ctx.seen_categories = dict(conn.execute(
        f"SELECT m.category_code, count(*) FROM core.transactions t JOIN core.merchants m USING (merchant_id) "
        f"WHERE t.customer_id=%(cust)s AND {_AT} GROUP BY 1", p).fetchall())
    ctx.incoming = [(r[0], float(r[1])) for r in conn.execute(
        f"SELECT event_time, amount FROM core.transactions t WHERE counterparty_account_id=%(acc)s "
        f"AND {_AT} AND event_time >= %(ts)s - interval '24 hours'", p)]
    return ctx
