import json
import logging
import time
import uuid
from typing import Annotated, Any

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from psycopg.rows import dict_row, tuple_row
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..agents.text_to_sql.executor import pseudonym
from ..config import get_settings
from ..db import health as db_health
from ..db import one
from ..logs import log, setup_logging
from ..observability import HTTP_LATENCY, setup_tracing
from ..privacy import ID_PATTERN, mask_obj
from .auth import Principal, viewer

logger = logging.getLogger("finsight.api")
MAX_LIMIT = 200


def create_app(dsn: str | None = None) -> FastAPI:
    setup_logging()
    app = FastAPI(title="FinSight API", version=__version__)
    dsn = dsn or get_settings().database_url

    def get_conn():
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            yield conn

    Conn = Annotated[psycopg.Connection, Depends(get_conn)]  # noqa: N806

    def err(status: int, code: str, message: str, request: Request):
        return JSONResponse(status_code=status, content={"error": {
            "code": code, "message": message, "request_id": getattr(request.state, "request_id", None)}})

    FastAPIInstrumentor.instrument_app(app, tracer_provider=setup_tracing())  # HTTP spans must use OUR provider, not the no-op global one

    @app.middleware("http")
    async def metrics_mw(request: Request, call_next):
        t0 = time.perf_counter()
        resp = await call_next(request)
        route = getattr(request.scope.get("route"), "path", "unmatched")
        HTTP_LATENCY.labels(request.method, route, str(resp.status_code)).observe(time.perf_counter() - t0)
        # structured request log: route template only - never the query string, path parameters or body (they can carry identifiers)
        log(logger, logging.INFO, "request", method=request.method, route=route, status=resp.status_code,
            ms=round((time.perf_counter() - t0) * 1000, 1), request_id=getattr(request.state, "request_id", None))
        return resp

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        try:
            st = get_settings()
            from ..pipeline.lag import consumer_lag
            consumer_lag(st.kafka_bootstrap, st.consumer_group, st.kafka_topic, timeout=2)  # refreshes finsight_consumer_lag
        except Exception:
            log(logger, logging.WARNING, "consumer_lag_unavailable")
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    EXEMPT = ("/health", "/ready", "/metrics", "/docs", "/openapi.json", "/redoc")

    @app.middleware("http")
    async def privacy_mw(request: Request, call_next):
        """Single choke point: every JSON response is pseudonymised unless an analyst explicitly asks to reveal (and that is audited)."""
        resp = await call_next(request)
        if request.url.path.startswith(EXEMPT) or not resp.headers.get("content-type", "").startswith("application/json"):
            return resp
        body = b"".join([c async for c in resp.body_iterator])
        headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
        if getattr(request.state, "reveal", False):
            who = getattr(request.state, "principal", None)
            log(logger, logging.WARNING, "identifier_reveal", path=request.url.path, actor=who.identity if who else None)
            try:
                with psycopg.connect(dsn, autocommit=True, connect_timeout=3) as c:
                    c.execute("INSERT INTO core.audit_events (run_id, event_type, actor, detail) VALUES ('access','identifier_reveal',%s,%s::jsonb)",
                              (who.identity if who else "unknown", json.dumps({"path": request.url.path})))
            except psycopg.Error:
                log(logger, logging.ERROR, "identifier_reveal_audit_failed")
            return Response(body, resp.status_code, headers, resp.media_type)
        try:
            masked = json.dumps(mask_obj(json.loads(body), get_settings().pii_mask_key), default=str).encode()
        except ValueError:
            masked = body
        return Response(masked, resp.status_code, headers, resp.media_type)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        resp = await call_next(request)
        resp.headers["x-request-id"] = request.state.request_id
        return resp

    @app.exception_handler(StarletteHTTPException)
    async def http_err(request: Request, exc: StarletteHTTPException):
        return err(exc.status_code, "http_error", str(exc.detail), request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log(logger, logging.ERROR, "unhandled", error=type(exc).__name__, request_id=request.state.request_id)
        return err(500, "internal_error", "Internal server error", request)  # no stack trace to client

    @app.exception_handler(psycopg.OperationalError)
    async def db_down(request: Request, exc: Exception):
        return err(503, "database_unavailable", "Database unavailable", request)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/ready")
    def ready(conn: Conn):
        h = db_health(conn)
        if not h["ready"]:
            raise HTTPException(503, f"migrations pending: {h['migrations_pending']}")
        return {"status": "ready", **h}

    @app.get("/auth/whoami")
    def whoami(p: Principal = viewer):
        return {"identity": p.identity, "role": p.role}

    def resolve(conn, table: str, col: str, value: str | None, p: Principal) -> str | None:
        """Filters accept pseudonyms (anyone) or raw ids (analysts only)."""
        if not value:
            return None
        if ID_PATTERN.fullmatch(value):
            if p.role != "analyst":
                raise HTTPException(403, "raw identifiers in filters require the analyst role; use the pseudonym shown in results")
            return value
        for (raw,) in conn.cursor(row_factory=tuple_row).execute(f"SELECT {col} FROM core.{table}"):
            if pseudonym(raw, get_settings().pii_mask_key) == value:
                return raw
        return "__no_such_id__"  # matches nothing

    @app.get("/transactions", dependencies=[viewer])
    def transactions(request: Request, conn: Conn, limit: int = Query(50, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0),
                     account_id: str | None = None, customer_id: str | None = None,
                     min_risk: float | None = Query(None, ge=0, le=1), since: str | None = None):
        pr = request.state.principal
        account_id = resolve(conn, "accounts", "account_id", account_id, pr)
        customer_id = resolve(conn, "customers", "customer_id", customer_id, pr)
        where: list[str] = []
        p: dict[str, Any] = {"limit": limit, "offset": offset}
        for col, val in (("t.account_id", account_id), ("t.customer_id", customer_id)):
            if val:
                where.append(f"{col} = %({col[2:]})s")
                p[col[2:]] = val
        if min_risk is not None:
            where.append("mp.risk_score >= %(min_risk)s")
            p["min_risk"] = min_risk
        if since:
            where.append("t.event_time >= %(since)s")
            p["since"] = since
        w = ("WHERE " + " AND ".join(where)) if where else ""
        base = ("FROM core.transactions t LEFT JOIN core.model_predictions mp USING (transaction_id) " + w)
        try:
            total = one(conn.execute(f"SELECT count(*) AS n {base}", p))["n"]
            rows = conn.execute(
                f"SELECT t.transaction_id, t.event_time, t.ingested_at, t.customer_id, t.account_id, "
                f"t.merchant_id, t.amount, t.currency, t.transaction_type, t.channel, t.status, "
                f"mp.risk_score, mp.model_version, mp.scored_at, mp.evidence->'signals' AS signals "
                f"{base} ORDER BY t.event_time DESC, t.transaction_id DESC LIMIT %(limit)s OFFSET %(offset)s", p
            ).fetchall()
        except psycopg.errors.DataError:
            raise HTTPException(422, "invalid filter value") from None
        return {"total": total, "limit": limit, "offset": offset, "items": rows}

    @app.get("/alerts", dependencies=[viewer])
    def alerts(conn: Conn, limit: int = Query(50, ge=1, le=MAX_LIMIT), offset: int = Query(0, ge=0),
               severity: str | None = Query(None, pattern="^(low|medium|high)$")):
        p = {"limit": limit, "offset": offset, "sev": severity}
        w = "WHERE a.severity = %(sev)s" if severity else ""
        total = one(conn.execute(f"SELECT count(*) AS n FROM core.fraud_alerts a {w}", p))["n"]
        rows = conn.execute(
            f"SELECT a.alert_id, a.transaction_id, a.severity, a.signals, a.created_at, t.event_time, "
            f"t.account_id, t.amount, mp.risk_score, mp.model_version FROM core.fraud_alerts a "
            f"JOIN core.transactions t USING (transaction_id) LEFT JOIN core.model_predictions mp "
            f"USING (transaction_id) {w} ORDER BY a.alert_id DESC LIMIT %(limit)s OFFSET %(offset)s", p
        ).fetchall()
        return {"total": total, "limit": limit, "offset": offset, "items": rows}

    @app.get("/alerts/{alert_id}", dependencies=[viewer])
    def alert_detail(alert_id: int, conn: Conn):
        a = conn.execute(
            "SELECT a.alert_id, a.severity, a.signals, a.created_at, row_to_json(t) AS transaction, "
            "mp.risk_score, mp.model_version, mp.scored_at FROM core.fraud_alerts a "
            "JOIN core.transactions t USING (transaction_id) LEFT JOIN core.model_predictions mp "
            "USING (transaction_id) WHERE a.alert_id = %s", (alert_id,)).fetchone()
        if not a:
            raise HTTPException(404, "alert not found")
        return a

    @app.get("/pipeline/stats", dependencies=[viewer])
    def pipeline_stats(conn: Conn):
        s = one(conn.execute(
            "SELECT count(*) FILTER (WHERE outcome='processed') AS processed, "
            "count(*) FILTER (WHERE outcome='duplicate') AS duplicate_events, "
            "coalesce(sum(duplicate_deliveries),0)::int AS duplicate_redeliveries, "
            "round(avg(scoring_latency_ms)::numeric,2) AS avg_scoring_ms, max(processed_at) AS last_processed_at, "
            "round(extract(epoch FROM avg(processed_at - event_time))::numeric,0) AS avg_event_to_processing_s "
            "FROM core.processed_events"))
        s["dead_letters"] = one(conn.execute("SELECT count(*) AS n FROM core.dead_letter_events"))["n"]
        s["alerts"] = one(conn.execute("SELECT count(*) AS n FROM core.fraud_alerts"))["n"]
        s["alert_threshold"] = get_settings().alert_threshold
        return s

    from .bi import build_router
    app.include_router(build_router(dsn))
    from .analytics import build_router as analytics_router
    from .investigations import build_router as inv_router
    app.include_router(inv_router())
    app.include_router(analytics_router(dsn))
    return app
