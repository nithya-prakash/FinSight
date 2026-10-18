import argparse
import json
import sys

import psycopg

from .config import get_settings
from .db import health, migrate
from .generator import GeneratorConfig, generate
from .generator.config import PROFILES
from .generator.seed import seed
from .generator.validate import validate
from .logs import setup_logging
from .pipeline.consumer import run_consumer
from .pipeline.producer import publish_dataset


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="finsight")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("health")
    g = sub.add_parser("generate", help="generate and validate in memory (no DB)")
    s = sub.add_parser("seed")
    for p in (g, s):
        p.add_argument("--profile", choices=PROFILES, default="dev")
        p.add_argument("--seed", type=int)
    s.add_argument("--reset", action="store_true")
    s.add_argument("--entities-only", action="store_true", help="skip transactions (stream them instead)")
    pr = sub.add_parser("produce", help="publish dataset events to Redpanda")
    pr.add_argument("--profile", choices=PROFILES, default="dev")
    pr.add_argument("--seed", type=int)
    pr.add_argument("--limit", type=int)
    pr.add_argument("--rate", type=float, help="events per second (default: as fast as possible)")
    ml = sub.add_parser("ml", help="model lifecycle")
    mls = ml.add_subparsers(dest="mlcmd", required=True)
    t = mls.add_parser("train", help="train + register a CANDIDATE (never promotes)")
    t.add_argument("--profile", choices=PROFILES, default="ml")
    t.add_argument("--seed", type=int, default=42)
    for nm in ("promote", "rollback"):
        q = mls.add_parser(nm)
        q.add_argument("--version", required=True)
        q.add_argument("--approver", required=True, help="name of the human approving this action")
    mo = mls.add_parser("monitor")
    mo.add_argument("--retrain-on-drift", action="store_true")
    mls.add_parser("status")
    ev = sub.add_parser("eval", help="run evaluations")
    ev.add_argument("suite", choices=["text2sql"])
    ev.add_argument("--mode", choices=["reference", "llm"], default="reference")
    ev.add_argument("--out", default="evals/reports")
    co = sub.add_parser("consume", help="run the scoring consumer")
    co.add_argument("--max-messages", type=int)
    co.add_argument("--idle-exit", type=float, help="exit after N idle seconds")
    a = ap.parse_args(argv)

    setup_logging()
    if a.cmd == "eval":
        from .agents.text_to_sql.agent import BIAgent
        from .agents.text_to_sql.evals import run_eval, summarize
        from .agents.text_to_sql.executor import ExecConfig
        from .agents.text_to_sql.llm import make_client
        st = get_settings()
        agent = BIAgent(st.bi_database_url, make_client(st.anthropic_api_key, st.bi_model), ExecConfig(mask_key=st.pii_mask_key))
        rep = run_eval(agent, a.mode)
        import pathlib
        pathlib.Path(a.out).mkdir(parents=True, exist_ok=True)
        pathlib.Path(a.out, f"text2sql_{a.mode}.json").write_text(json.dumps(rep, indent=2, default=str))
        pathlib.Path(a.out, f"text2sql_{a.mode}.md").write_text(summarize(rep))
        print(summarize(rep))
        return 0 if not rep["failures"] else 1
    if a.cmd == "ml":
        return _ml(a)
    if a.cmd == "consume":
        run_consumer(get_settings(), max_messages=a.max_messages, idle_exit_s=a.idle_exit)
        return 0
    if a.cmd in ("generate", "seed", "produce"):
        cfg = PROFILES[a.profile].model_copy()
        if a.seed is not None:
            cfg = GeneratorConfig(**{**cfg.model_dump(), "seed": a.seed})
        ds = generate(cfg)
        report = validate(ds)
        report.update(dataset_id=ds.dataset_id, fingerprint=ds.fingerprint())
        print(json.dumps(report, indent=2))
        if not report["ok"]:
            return 1
        if a.cmd == "generate":
            return 0
        if a.cmd == "produce":
            st = get_settings()
            print(json.dumps({"published": publish_dataset(ds, st.kafka_bootstrap, st.kafka_topic,
                                                           limit=a.limit, rate_per_s=a.rate)}))
            return 0
    with psycopg.connect(get_settings().database_url) as conn:
        if a.cmd == "migrate":
            print(json.dumps({"applied": migrate(conn)}))
        elif a.cmd == "health":
            h = health(conn)
            print(json.dumps(h, indent=2))
            return 0 if h["ready"] else 1
        else:
            print(json.dumps(seed(conn, ds, reset_first=a.reset, entities_only=a.entities_only)))
    return 0


def _ml(a) -> int:
    from .ml import registry
    from .ml.data import frame_from_dataset
    from .ml.monitoring import MonitorConfig
    from .ml.retrain import monitor_and_maybe_retrain
    from .ml.train import train_candidate
    st = get_settings()
    uri = st.mlflow_tracking_uri or "http://localhost:5000"
    client = registry.configure(uri)
    with psycopg.connect(st.database_url, autocommit=True) as conn:
        if a.mlcmd == "train":
            res = train_candidate(frame_from_dataset(generate(PROFILES[a.profile])), tracking_uri=uri, seed=a.seed, conn=conn)
            m = res["report"]["models"]
            print(json.dumps({"candidate": res["label"], "acceptance": res["report"]["acceptance"],
                              "validation_pr_auc": {k: round(v["validation"]["pr_auc"], 4) for k, v in m.items()},
                              "holdout_pr_auc": {k: round(v["holdout"]["pr_auc"], 4) for k, v in m.items()}}, indent=2))
        elif a.mlcmd in ("promote", "rollback"):
            try:
                (registry.promote if a.mlcmd == "promote" else registry.rollback)(client, a.version, a.approver, conn)
            except registry.PromotionError as e:
                print(f"REFUSED: {e}")
                return 2
            print(f"{a.mlcmd} v{a.version} by {a.approver}: ok")
        elif a.mlcmd == "monitor":
            out = monitor_and_maybe_retrain(conn, uri, MonitorConfig(html_dir="/tmp/drift"), retrain_on_drift=a.retrain_on_drift)
            print(json.dumps(out, indent=2, default=str))
        else:
            print(json.dumps({"approved": registry.approved_version(client),
                              "events": [dict(zip(("type", "version", "actor", "at"), r, strict=True)) for r in conn.execute(
                                  "SELECT event_type, model_version, actor, created_at::text FROM core.model_events ORDER BY event_id DESC LIMIT 10")]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
