import uuid

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.rows import dict_row

from ...config import Settings
from ...db import one
from ...observability import INVESTIGATIONS, span
from .models import DECISIONS
from .workflow import Deps, audit, build_graph


class InvestigationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class InvestigatorService:
    def __init__(self, deps: Deps):
        self.d = deps
        with self._saver() as s:
            s.setup()  # creates the LangGraph checkpoint tables (library-managed)

    def _saver(self):
        conn = psycopg.connect(self.d.owner_dsn, autocommit=True, row_factory=dict_row, prepare_threshold=None)
        return _Saver(conn)

    def start(self, alert_id: int) -> dict:
        with span("investigation.run", alert_id=alert_id):
            return self._start(alert_id)

    def _start(self, alert_id: int) -> dict:
        with psycopg.connect(self.d.owner_dsn, autocommit=True) as c:
            if not c.execute("SELECT 1 FROM core.fraud_alerts WHERE alert_id=%s", (alert_id,)).fetchone():
                raise InvestigationError("not_found", f"alert {alert_id} not found")
            # a crashed run must not block the alert forever
            c.execute("UPDATE core.investigation_runs SET state='failed', updated_at=now() WHERE alert_id=%s AND state='running' "
                      "AND updated_at < now() - interval '10 minutes'", (alert_id,))
            run_id = uuid.uuid4().hex
            got = c.execute("INSERT INTO core.investigation_runs (run_id, alert_id, state) VALUES (%s,%s,'running') "
                            "ON CONFLICT (alert_id) WHERE state IN ('running','pending_review') DO NOTHING RETURNING run_id", (run_id, alert_id)).fetchone()
            if got is None:  # idempotent replay: return the active run instead of starting a duplicate
                existing = one(c.execute("SELECT run_id FROM core.investigation_runs WHERE alert_id=%s AND state IN ('running','pending_review')", (alert_id,)))
                return {**self.get(existing[0]), "idempotent_replay": True}
            row = c.execute("SELECT case_id FROM core.investigation_cases WHERE alert_id=%s AND status IN ('open','escalated') "
                            "ORDER BY case_id DESC LIMIT 1", (alert_id,)).fetchone()  # a closed case is never silently reused
            case_id = row[0] if row else one(c.execute("INSERT INTO core.investigation_cases (alert_id) VALUES (%s) RETURNING case_id", (alert_id,)))[0]
        try:
            audit(self.d.owner_dsn, run_id, case_id, "investigation_started", "system", {"alert_id": alert_id})
            with self._saver() as saver:
                graph = build_graph(self.d, saver)
                graph.invoke({"run_id": run_id, "alert_id": alert_id, "case_id": case_id}, {"configurable": {"thread_id": run_id}})
        except Exception:
            self._set_state(run_id, "failed")
            raise
        self._set_state(run_id, "pending_review")
        out = self.get(run_id)
        INVESTIGATIONS.labels(out["llm_status"]).inc()
        return out

    def _set_state(self, run_id: str, state: str) -> None:
        with psycopg.connect(self.d.owner_dsn, autocommit=True) as c:
            c.execute("UPDATE core.investigation_runs SET state=%s, updated_at=now() WHERE run_id=%s", (state, run_id))

    def get(self, run_id: str) -> dict:
        with psycopg.connect(self.d.owner_dsn, row_factory=dict_row) as c:
            r = c.execute("SELECT run_id, case_id, alert_id, status, llm_status, report, created_at, decided_at FROM core.case_reports WHERE run_id=%s", (run_id,)).fetchone()
            if not r:
                run = c.execute("SELECT run_id, alert_id, state FROM core.investigation_runs WHERE run_id=%s", (run_id,)).fetchone()
                if run and run["state"] == "running":
                    return {"run_id": run_id, "alert_id": run["alert_id"], "status": "running", "report": None}
                raise InvestigationError("not_found", "investigation not found")
            ev = c.execute("SELECT event_type, actor, detail, created_at FROM core.audit_events WHERE run_id=%s ORDER BY event_id", (run_id,)).fetchall()
            case = one(c.execute("SELECT status FROM core.investigation_cases WHERE case_id=%s", (r["case_id"],)))
        return {**r, "case_status": case["status"], "audit": ev}

    def list(self, limit: int = 50) -> list[dict]:
        with psycopg.connect(self.d.owner_dsn, row_factory=dict_row) as c:
            return c.execute(
                "SELECT r.run_id, r.case_id, r.alert_id, r.status, r.llm_status, r.created_at, r.decided_at, k.status AS case_status, "
                "(r.report->'potential_exposure'->>'amount_eur')::float AS exposure_eur FROM core.case_reports r "
                "JOIN core.investigation_cases k USING (case_id) ORDER BY r.report_id DESC LIMIT %s", (limit,)).fetchall()

    def decide(self, run_id: str, decision: str, analyst: str, note: str | None = None) -> dict:
        analyst = (analyst or "").strip()
        if not analyst or len(analyst) > 100:
            raise InvestigationError("analyst_required", "a named human analyst is required to record a decision")
        if decision not in DECISIONS:
            raise InvestigationError("invalid_decision", f"decision must be one of {sorted(DECISIONS)}")
        cur = self.get(run_id)
        claim = uuid.uuid4().hex
        with psycopg.connect(self.d.owner_dsn, autocommit=True) as c:
            won = c.execute("UPDATE core.case_reports SET decision_claim=%s, claimed_at=now() WHERE run_id=%s AND status='pending_review' "
                            "AND (decision_claim IS NULL OR claimed_at < now() - interval '2 minutes') RETURNING report_id", (claim, run_id)).fetchone()
        if won is None:  # lost the race, already decided, or still running
            if cur["status"] == "pending_review":
                raise InvestigationError("decision_in_progress", "another decision is being recorded for this investigation")
            raise InvestigationError("already_decided", f"investigation already decided ({cur['status']})")
        try:
            with self._saver() as saver:
                graph = build_graph(self.d, saver)  # fresh graph + saver: resumes from the persisted checkpoint
                graph.invoke(Command(resume={"decision": decision, "analyst": analyst, "note": (note or "")[:500]}),
                             {"configurable": {"thread_id": run_id}})
        except Exception:
            with psycopg.connect(self.d.owner_dsn, autocommit=True) as c:  # release so the decision can be retried
                c.execute("UPDATE core.case_reports SET decision_claim=NULL, claimed_at=NULL WHERE run_id=%s AND decision_claim=%s", (run_id, claim))
            raise
        return self.get(run_id)


class _Saver:
    def __init__(self, conn):
        self.conn, self.saver = conn, PostgresSaver(conn)

    def __enter__(self):
        return self.saver

    def __exit__(self, *a):
        self.conn.close()


def make_service(settings: Settings, llm) -> InvestigatorService:
    return InvestigatorService(Deps(settings, llm, settings.investigator_database_url, settings.database_url))
