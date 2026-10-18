# Fraud model

## Pipeline
`generator -> frame -> OfflineContexts -> featurize (fs-1) -> chronological split -> {rules, logistic regression, XGBoost}`
Everything is logged to MLflow (`finsight-fraud` experiment): params, per-model validation/holdout metrics,
`evaluation.json` (periods, dataset id, feature-set version, thresholds, cost model, acceptance verdict),
`features.json`, a training reference sample (`reference.csv.gz`) and the model. Run: `finsight ml train --profile ml`.

## Leakage controls (tested)
* One feature definition (`ml/features.py::featurize`) used offline and online; contexts only contain events strictly before the current `(event_time, transaction_id)`.
* Tests: labels flipped -> features identical; events after a cut-off removed -> earlier features identical; online (DB `fetch_context`) vs offline features equal on 150+ events including every fraud scenario type.
* Labels/scenario names are not in `FEATURES` (asserted). Scaler/model fit on the training period only.
* Split: 60% train / 20% validation / 20% holdout by time, with a 24 h gap after each boundary (label-maturity policy). Threshold and acceptance use validation only; holdout is reported once per training run.

## Cost model (configurable, `CostModel`)
EUR 5 per alert reviewed + EUR 2 customer friction per false alert + 100% of a missed fraud amount. The alert
threshold is the validation cost minimiser (not 0.5). Also reported: precision, recall, F1, FPR, PR-AUC, confusion
matrix, precision/recall at a 1% alert budget.

## Acceptance criteria for a candidate (recorded as MLflow tags)
Candidate (validation) expected cost <= reference AND PR-AUC >= reference - 0.01 AND leakage check passed AND holdout recorded.
Reference = the currently approved model scored on the same validation window, or the `rules-v1` baseline (threshold 0.5) when none is approved.

## Promotion
New versions only get alias `candidate`. `finsight ml promote --version N --approver "Name"` refuses without a passing
evaluation and a named approver; `rollback` only to a previously approved version. Events are stored in `core.model_events`.
The streaming consumer (`SCORING_MODE=ml`) loads alias `approved`, re-checks every 60 s, and **falls back to the rules-only
baseline with a log line** when none is approved or the registry is unreachable.

## Limitations of the synthetic data
Scenarios are simple and well separated, so scores on this data are optimistic and say nothing about real fraud performance.
`device_known` is a generator artifact (fraud uses fresh devices); the report includes an `xgb_no_device` ablation so the
dependency is visible. Rule scores are ordinal, not calibrated probabilities.
