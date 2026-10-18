# Final verification report

Verified snapshot: hash `148af19e07ed3d2d` (scope: all files except `docs/`, `.git`, build output, caches and the local `.env`). The hash was identical before the run,
on the fresh copy used for verification, after the run, and in the original repository after cleanup.
After verification, the only changes were to two `.gitignore` files (ignoring dbt's machine-specific `.user.yml`); a recursive diff of the verified copy against the repository confirmed this.
Added afterwards (no application code changed): `LICENSE` (MIT), the README rewrite, `docs/demo.mp4` and `docs/demo.gif` with `scripts/record_demo.py` (ruff-checked), and doc edits. A recursive diff against the verified copy lists only these files.
Environment: Docker Desktop on macOS arm64; fresh Compose project with empty volumes; all four images rebuilt with `--no-cache --pull`.
Everything below was executed by a single scripted run; `PASS` means the command ran and its result was checked, nothing is inferred from logs that only show a start.

## Results
| Check | Result | Level |
|---|---|---|
| Image builds (tools, api, fraud-consumer, web), no cache | built | live |
| `npm ci`; `tsc --noEmit`; `vitest run` | 233 packages; no type errors; 9 of 9 tests | unit |
| `next build` (production) | compiled successfully | live |
| `npm audit` (production and development dependencies) | 0 vulnerabilities in both | live |
| Migrations on an empty database | 6 of 6 (0001 to 0006) | live |
| Seed; stream 3,000 events through Redpanda and the consumer | 3,000 processed, 69 alerts, 0 dead letters | live end-to-end |
| dbt build (models and data tests) | 105 of 105 passed, 0 errors | integration |
| `ruff check` | all checks passed | static |
| `mypy` (54 source files) | no issues | static |
| Security gate (`make security`) | 49 passed | unit + real PostgreSQL |
| Full Python suite (`make test`) | **187 passed, 4 skipped** | unit, mocked LLM, real-infrastructure integration |
| Access matrix (anon / invalid / viewer / analyst) over 15 endpoints | all as designed (401, 401, 200 or 403, 200); `/health` and `/ready` public | live |
| Identifier privacy: 24 API responses, error bodies, `/metrics`, api/consumer/web logs | no raw identifiers or keys anywhere | live |
| `reveal_ids`: viewer 403, analyst 200, audit row written (actor from the key) | pass | live |
| 6 simultaneous decisions on one investigation | exactly one 200 and five 409; one decision event; actor taken from the key; repeat start returns the same run | live |
| Database read-only enforcement (`finsight_bi`) | reads of `core`/`eval` denied; INSERT and DROP rejected (read-only transaction); statement timeout cancels `pg_sleep(8)`; in a READ WRITE transaction INSERT gets "permission denied" and DROP "must be owner" | live |
| Consumer lag | 0 baseline; 253 with the consumer stopped; back to 0 after restart | live |
| Dead letters and data-quality metrics | 3 dead letters (malformed JSON, invalid UTF-8, schema violation); metrics match | live |
| Scoring p50/p95 | API equals an independent SQL `percentile_cont` | live |
| Consumer healthcheck | healthy; unhealthy when the poll loop is frozen (`docker pause`, which also freezes the probe); healthy after unpause | live |
| Jaeger (real OTLP export) | HTTP request spans, `bi.ask`, investigator node spans, nesting under the request span; no identifiers, SQL or question text in attributes | live |
| No API key | `/bi/status` reports LLM unavailable; `/bi/query` returns `llm_unavailable` with no rows; investigations are facts-only | live |
| Invalid key against the real Anthropic endpoint, and real timeout (`make live-network`) | `AuthenticationError`, key not leaked; timeout reported as unavailable (2 passed) | real SDK, real network, no spend |
| Real Claude SQL generation and investigator analysis | **NOT RUN** (no `ANTHROPIC_API_KEY`) | n/a |

The 4 skipped tests are the opt-in ones: 2 real-Claude tests and the 2 network tests (the network tests were run separately above).

## Defects found and fixed during verification
Web Docker build failure (npm peer conflict); missing database URL for the eval command; MLflow outage made `/models` hang; closed cases silently reused; HTTP request spans not exported (wrong tracer provider);
agent crash on a non-string `sql` model field; uvicorn access logs would have recorded query strings; npm advisories in dev tooling (resolved by upgrading vitest, vite and the React plugin);
two defects in the verification script itself (wrong Compose project stopped; unquoted filename) and one wrong expectation for read-only write rejections. All fixed and re-verified in this run.

## Not verified / limitations
* **Real Claude answer quality is unverified.** No successful live Anthropic call was made. The mock-transport and scripted-stand-in tests prove failure handling and orchestration only. Run `make live-claude` with a key to close this.
* The GitHub Actions workflow has not run on GitHub (its commands were run locally). No `act` run.
* Authentication is shared API keys with two roles; the dashboard acts as one analyst. No per-user login, SSO, TLS or secrets manager.
* `ruff format --check` fails on most files (the code was never auto-formatted); lint and mypy are enforced, formatting is not.
* Synthetic data only: model scores say nothing about real fraud performance. Not production-ready. No load or performance testing, no browser end-to-end tests.
