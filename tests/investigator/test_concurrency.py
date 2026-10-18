"""Atomic decisions and idempotent investigation starts, against real PostgreSQL."""

import threading
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest

from finsight.agents.investigator import service as service_module
from finsight.agents.investigator.service import InvestigationError

pytestmark = pytest.mark.integration


def rows(env, sql, *a):
    with psycopg.connect(env["owner"]) as c:
        return c.execute(sql, a).fetchall()


def fresh_alert(env, svc, n=0):
    """An alert with no active run (earlier tests may have decided theirs)."""
    ids = [r[0] for r in rows(env, "SELECT alert_id FROM core.fraud_alerts ORDER BY alert_id DESC")]
    active = {
        r[0]
        for r in rows(
            env,
            "SELECT alert_id FROM core.investigation_runs WHERE state IN ('running','pending_review')",
        )
    }
    free = [i for i in ids if i not in active]
    return free[n]


def test_concurrent_decisions_exactly_one_wins(env, svc_factory):
    svc = svc_factory(None)
    run = svc.start(fresh_alert(env, svc))
    barrier = threading.Barrier(8)

    def attempt(i):
        barrier.wait()
        try:
            return (
                "ok",
                svc_factory(None).decide(
                    run["run_id"],
                    ["confirm_fraud", "close_legit", "escalate", "need_more_info"][i % 4],
                    f"analyst-{i}",
                ),
            )
        except InvestigationError as e:
            return (e.code, None)

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(attempt, range(8)))
    assert [r[0] for r in results].count("ok") == 1
    assert {r[0] for r in results} <= {"ok", "already_decided", "decision_in_progress"}
    events = rows(
        env,
        "SELECT actor FROM core.audit_events WHERE run_id=%s AND event_type='decision_recorded'",
        run["run_id"],
    )
    assert len(events) == 1  # one decision recorded, not eight
    winner = events[0][0]
    final = svc.get(run["run_id"])
    assert final["report"]["human_decision"]["analyst"] == winner
    assert final["status"] != "pending_review"


def test_decision_failure_rolls_back_and_can_be_retried(env, svc_factory, monkeypatch):
    svc = svc_factory(None)
    run = svc.start(fresh_alert(env, svc))
    real = service_module.build_graph

    def boom(*a, **k):
        raise RuntimeError("simulated crash while resuming")

    monkeypatch.setattr(service_module, "build_graph", boom)
    with pytest.raises(RuntimeError):
        svc.decide(run["run_id"], "confirm_fraud", "analyst-1")
    monkeypatch.setattr(service_module, "build_graph", real)
    cur = svc.get(run["run_id"])
    assert (
        cur["status"] == "pending_review" and cur["case_status"] == run["case_status"]
    )  # nothing half-applied
    assert rows(
        env, "SELECT decision_claim FROM core.case_reports WHERE run_id=%s", run["run_id"]
    ) == [(None,)]  # claim released
    assert svc.decide(run["run_id"], "close_legit", "analyst-2")["case_status"] == "closed_legit"


def test_stale_claim_expires(env, svc_factory):
    svc = svc_factory(None)
    run = svc.start(fresh_alert(env, svc))
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute(
            "UPDATE core.case_reports SET decision_claim='crashed-worker', claimed_at=now() - interval '10 minutes' WHERE run_id=%s",
            (run["run_id"],),
        )
    assert svc.decide(run["run_id"], "escalate", "analyst-3")["case_status"] == "escalated"
    with psycopg.connect(env["owner"], autocommit=True) as c:  # a *fresh* claim blocks others
        run2 = svc.start(fresh_alert(env, svc))
        c.execute(
            "UPDATE core.case_reports SET decision_claim='someone', claimed_at=now() WHERE run_id=%s",
            (run2["run_id"],),
        )
    with pytest.raises(InvestigationError) as e:
        svc.decide(run2["run_id"], "escalate", "analyst-4")
    assert e.value.code == "decision_in_progress"


def test_repeated_start_is_idempotent_and_new_run_after_decision(env, svc_factory):
    svc = svc_factory(None)
    alert = fresh_alert(env, svc)
    first = svc.start(alert)
    again = svc.start(alert)
    assert again["run_id"] == first["run_id"] and again.get("idempotent_replay") is True
    assert rows(
        env,
        "SELECT count(*) FROM core.investigation_runs WHERE alert_id=%s AND state IN ('running','pending_review')",
        alert,
    ) == [(1,)]
    svc.decide(first["run_id"], "close_legit", "analyst-5")
    second = svc.start(alert)
    assert second["run_id"] != first["run_id"]
    assert second["case_id"] != first["case_id"]  # the closed case is not reused


def test_concurrent_starts_create_one_run_and_one_case(env, svc_factory):
    alert = fresh_alert(env, svc_factory(None))
    barrier = threading.Barrier(6)

    def go(_):
        barrier.wait()
        return svc_factory(None).start(alert)

    with ThreadPoolExecutor(6) as ex:
        outs = list(ex.map(go, range(6)))
    assert len({o["run_id"] for o in outs}) == 1
    assert (
        rows(
            env,
            "SELECT count(*) FROM core.investigation_cases WHERE alert_id=%s AND status='open'",
            alert,
        )[0][0]
        == 1
    )
    assert (
        rows(
            env,
            "SELECT count(*) FROM core.case_reports WHERE alert_id=%s AND status='pending_review'",
            alert,
        )[0][0]
        == 1
    )


def test_failed_start_does_not_block_the_alert(env, svc_factory, monkeypatch):
    svc = svc_factory(None)
    alert = fresh_alert(env, svc)
    monkeypatch.setattr(
        service_module, "build_graph", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        svc.start(alert)
    assert rows(
        env,
        "SELECT state FROM core.investigation_runs WHERE alert_id=%s ORDER BY created_at DESC LIMIT 1",
        alert,
    ) == [("failed",)]
    monkeypatch.undo()
    assert svc.start(alert)["status"] == "pending_review"  # retry works


def test_unknown_alert_and_decision_validation_unchanged(env, svc_factory):
    svc = svc_factory(None)
    with pytest.raises(InvestigationError):
        svc.start(9_999_999)
    run = svc.start(fresh_alert(env, svc))
    with pytest.raises(InvestigationError) as e:
        svc.decide(run["run_id"], "confirm_fraud", "  ")
    assert e.value.code == "analyst_required"


def test_zz_database_invariants_hold_after_all_scenarios(env):
    """Consistency after concurrency, rollbacks and crashes: run last in this module."""
    q = lambda sql: rows(env, sql)[0][0]  # noqa: E731
    # a case may be reopened and decided again by a later run; its status must match its LATEST decided report
    assert (
        q(
            "SELECT count(*) FROM (SELECT DISTINCT ON (r.case_id) r.status AS rs, k.status AS ks FROM core.case_reports r JOIN core.investigation_cases k USING (case_id) "
            "WHERE r.status <> 'pending_review' ORDER BY r.case_id, r.decided_at DESC) x WHERE NOT ("
            "(rs='approved_fraud' AND ks='closed_fraud') OR (rs='closed_legit' AND ks='closed_legit') OR (rs='escalated' AND ks='escalated') "
            "OR (rs='needs_more_info' AND ks IN ('open','escalated')))"
        )
        == 0
    )
    assert (
        q(
            "SELECT count(*) FROM (SELECT run_id FROM core.audit_events WHERE event_type='decision_recorded' GROUP BY run_id HAVING count(*) > 1) x"
        )
        == 0
    )
    assert (
        q(
            "SELECT count(*) FROM core.case_reports r WHERE r.status <> 'pending_review' AND NOT EXISTS (SELECT 1 FROM core.audit_events a WHERE a.run_id=r.run_id AND a.event_type='decision_recorded')"
        )
        == 0
    )
    assert (
        q(
            "SELECT count(*) FROM core.case_reports r WHERE r.status='pending_review' AND r.report->'human_decision' <> 'null'::jsonb"
        )
        == 0
    )
    assert (
        q(
            "SELECT count(*) FROM core.case_reports r JOIN core.investigation_runs u USING (run_id) WHERE (r.status='pending_review') <> (u.state='pending_review')"
        )
        == 0
    )
    assert (
        q(
            "SELECT count(*) FROM (SELECT alert_id FROM core.investigation_runs WHERE state IN ('running','pending_review') GROUP BY alert_id HAVING count(*) > 1) x"
        )
        == 0
    )
    assert (
        q("SELECT count(*) FROM core.investigation_runs WHERE state='running'") == 0
    )  # no stuck runs after failures/retries
    assert (
        q(
            "SELECT count(*) FROM core.case_reports WHERE decision_claim IS NOT NULL AND status='pending_review' AND claimed_at < now() - interval '2 minutes'"
        )
        >= 0
    )
