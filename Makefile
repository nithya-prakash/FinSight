RUN = docker compose run --rm tools
RUNE = docker compose run --rm
.PHONY: live-claude live-network up down migrate seed health test test-web lint typecheck lint-web eval security ci lock demo

up: ; docker compose up -d --wait postgres redpanda
down: ; docker compose down
migrate: ; $(RUN) finsight migrate
seed: ; $(RUN) finsight seed --reset --entities-only
health: ; $(RUN) finsight health
demo: ; $(RUN) finsight produce --profile dev --limit 3000 && $(RUN) sh -c 'dbt build --project-dir warehouse/dbt --profiles-dir warehouse/dbt --target-path /tmp/t --log-path /tmp/l'
lint: ; $(RUN) ruff check src tests orchestration
typecheck: ; $(RUN) mypy
lint-web: ; cd apps/web && npx tsc --noEmit
test: ; $(RUN) pytest -q
test-web: ; cd apps/web && npm test
security: ; $(RUN) pytest -q tests/bi/test_policy.py tests/bi/test_agent.py tests/investigator/test_access_and_privacy.py tests/investigator/test_concurrency.py -k "attack or role or masked or pseudonym or requires or rejects or leak or reveal or identity or auth or concurrent or exactly or idempotent or raw"
eval: ; $(RUN) finsight eval text2sql --mode reference
ci: lint typecheck lint-web test test-web
lock: ; $(RUN) sh -c 'pip freeze --exclude-editable | grep -v "^finsight"' > constraints.txt
# Opt-in tests against the REAL Anthropic API (cost-capped by LIVE_CLAUDE_MAX_USD, default 0.50 estimated USD). Needs ANTHROPIC_API_KEY in your shell.
live-claude: ; $(RUNE) -e ANTHROPIC_API_KEY -e RUN_LIVE_CLAUDE=1 -e LIVE_CLAUDE_MAX_USD -e LIVE_CLAUDE_MODEL tools pytest -q -m live_claude tests/bi/test_real_claude_sql.py tests/investigator/test_real_claude_investigator.py
live-network: ; $(RUNE) -e RUN_LIVE_NETWORK=1 tools pytest -q -m live_network tests/test_real_network_failures.py
