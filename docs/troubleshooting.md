# Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `Cannot connect to the Docker daemon` | Start Docker Desktop (`open -a Docker`) and wait for `docker info`. |
| Port already allocated (5433/8000/3001/19092) | Stop the other process or change the host port mapping in `docker-compose.yml`. |
| 401 / 403 / 503 "authentication is not configured" | Send `X-API-Key` (see `AUTH_KEYS` in `.env`); viewer keys cannot use `/bi` or `/investigations`; with no `AUTH_KEYS` the API fails closed (use `AUTH_DISABLED=true` only for a local demo). |
| Filtering by account returns 403 / nothing | Filters take the pseudonym shown in results (`id_...`); raw ids need the analyst role. |
| `/ready` returns 503 "migrations pending" | `make migrate`. |
| Dashboard shows "API unreachable" / 502 | `docker compose ps`; the `web` container needs `api` healthy. Check `docker compose logs api`. |
| Dashboard empty / Analytics blank | No data yet: `make seed && make demo`. The AI analyst and marts need `dbt build` (part of `make demo`). |
| `permission denied for table ...` in the AI analyst | The marts were created before migration 0004 granted access: re-run `make migrate` then `dbt build`. |
| Consumer processes nothing | Check topic/offset: the group has committed offsets for old events. Produce new events or use a new `CONSUMER_GROUP`. Dead letters: `core.dead_letter_events`. |
| Analyst page says "Live LLM unavailable" | Expected without `ANTHROPIC_API_KEY` (section 9 of README). |
| Models page: "registry not connected" | `docker compose --profile ml up -d mlflow` (first start ~30 s). |
| Containers killed / very slow | Raise Docker memory to >= 6 GB; skip the `warehouse`/`ml` profiles. |
| `dagster` shows no assets | `DAGSTER_DBT_PARSE_PROJECT_ON_LOAD=1` is set in compose; restart `dagster-webserver`. |
| Web build fails on `npm ci` | Use Node 22; delete `apps/web/node_modules`. |
