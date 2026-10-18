# Model monitoring and retraining

`finsight ml monitor [--retrain-on-drift]` compares the latest scored window (features stored in
`model_predictions.evidence.ml.features`) with the training reference, using Evidently's K-S drift tests.

| Signal | Meaning | Flag |
|---|---|---|
| feature drift | inputs moved vs training | share of drifted features >= `drift_share` (0.3), per-column p < 0.01 |
| prediction drift | score distribution moved | p(score) < 0.01 |
| data quality | non-finite values, > 5% outside the training range | listed issues |
| performance | PR-AUC on matured labels vs validation PR-AUC | degraded if drop > 25%; **NULL when fewer than 20 labeled positives** |

Drift does not prove the model is wrong; only the performance signal measures accuracy. Any signal can request a retraining
**candidate** (never a deployment): the candidate is trained, evaluated against the approved model, registered with alias
`candidate` and still needs `promote` with a human approver. Results are stored in `core.monitoring_runs` (HTML report optional).
All thresholds are fields of `MonitorConfig`.
