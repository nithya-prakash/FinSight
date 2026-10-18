# Evaluation

## Text-to-SQL (`finsight eval text2sql --mode reference|llm`)
* `evals/text_to_sql/golden.yaml`: 15 deterministic questions (schema selection, joins, metric definitions, temporal filters, aggregations) with
  reference SQL, plus 3 ambiguous, 3 unanswerable and 2 prompt-injection questions that need a live LLM. Few-shot prompt examples are kept separate.
* Reference SQL is checked against **independent Python calculations** from the fixture dataset (`tests/bi/test_golden.py`).
* `--mode reference` validates the golden set + pipeline (no LLM). It is not an accuracy measure. `--mode llm` scores execution accuracy of
  generated SQL (result-set comparison, order only when `ordered: true`) per category with denominators and failure kinds; it needs `ANTHROPIC_API_KEY`
  and is not part of mandatory CI. Without a key, LLM cases are reported as "not evaluated".
* Reports: `evals/reports/text2sql_<mode>.json|md`. Security attacks are a hard gate in the pytest suite.
* **Status:** no live Claude run has been executed in this environment (no API key). Orchestration tests use a scripted fake LLM and say so.

## Real-Claude tests (opt-in, budgeted)
Two test modules call the real Anthropic API and are **skipped unless both `ANTHROPIC_API_KEY` is set and `RUN_LIVE_CLAUDE=1`** (never part of default or CI runs):
`tests/bi/test_real_claude_sql.py` (4 questions with independently computed expected answers + 1 unanswerable question, all through the full SQL policy, read-only role and masking) and
`tests/investigator/test_real_claude_investigator.py` (one investigation; every claim re-checked against the retrieved evidence, identifiers never in prompts, no case action taken).
Setup and run (synthetic data only):
```bash
export ANTHROPIC_API_KEY=sk-ant-...        # in your shell only; never commit it
export LIVE_CLAUDE_MAX_USD=0.50            # hard cap on ESTIMATED cost (pricing.yaml placeholders), checked before every call; also max 14 calls / 80k tokens
make live-claude                           # writes evals/reports/live_claude_*.json (model, latency, tokens, estimated cost per call)
make live-network                          # no key needed: real endpoint + deliberately invalid key + real timeout
```
Status: `live-network` tests pass (invalid key -> `AuthenticationError`, key not leaked; timeout -> reported unavailable). The `live-claude` tests have **never been run** (no key available), so real answer quality is unverified.
