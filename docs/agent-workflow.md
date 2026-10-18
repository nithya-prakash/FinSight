# Fraud investigator workflow (LangGraph)

`retrieve -> analyze -(conditional retry, max 2)-> draft -> persist -> human_review (interrupt) -> record`

* **Evidence** (`agents/investigator/evidence.py`): deterministic SQL through the `finsight_investigator` role, which cannot read `eval.fraud_labels` or `customers.full_name`.
  Every item has an id (`E1..`), source and basis: `database_fact`, `model_output`, `observed_relationship` or `inferred_suspicious`.
* **Analysis** (Claude, structured tool output): flag reasons, supporting and contradicting evidence, hypotheses, confidence, missing information, next steps.
  `validation.py` drops claims without valid evidence ids or with numbers absent from the cited evidence, caps confidence to low when the customer has < 5 prior transactions,
  and re-asks (bounded, 2 attempts). If nothing usable remains, the report falls back to database facts and model outputs only (`llm_status: degraded`).
  Without an API key it says `unavailable` and never writes narrative.
* **Report**: separate sections for database facts, model outputs, observed vs inferred relationships, agent analysis (hypotheses labelled), exposure (computed in SQL), timeline,
  missing information, limitations, and `human_decision`. It is labelled an educational SAR-style draft, not a regulatory filing.
* **Human approval**: the graph pauses at `interrupt()`; state is checkpointed in PostgreSQL (LangGraph `PostgresSaver`, library-managed tables), so a new process can resume it.
  `POST /investigations/{id}/decision` needs a named analyst and one of `confirm_fraud | close_legit | escalate | need_more_info`; only then the case status changes. A decision can be made once.
* The workflow has no capability to block payments, freeze accounts, contact customers or file reports. Every step is written to `core.audit_events`.
* Prompt-injection stance: retrieved text is passed only inside `<evidence_data>`, the system prompt says it is data, and the model has no tools beyond returning structured output.
* **Status**: orchestration is verified with a scripted fake LLM; no live Claude run was executed (no API key).
