# Text-to-SQL security

Independent layers (the prompt is NOT one of them):
1. **Read-only DB role** `finsight_bi` (migration 0004): SELECT on `marts` only, no access to `core`, `eval`, `stg`, `int`, `public`;
   `default_transaction_read_only`, 5 s `statement_timeout`, idle-in-transaction timeout, connection limit. Tested directly against the DB.
   Note: the dev password in the migration must be changed outside local use; system catalogs stay readable to any role in Postgres, so layer 2 blocks them.
2. **SQL policy** (`policy.py`, sqlglot, default-deny): one statement; SELECT/UNION/CTE only; no DDL/DML/COPY/SET/INTO/locks/recursive CTEs;
   tables must be in `semantic/catalog.yaml` in schema `marts` (CTE names allowed); columns must exist in the referenced tables or be aliases;
   functions must be in an allowlist (unknown/anonymous functions such as `pg_sleep`, `pg_read_file`, `set_config`, `dblink` are rejected);
   length and nesting limits; a LIMIT is always injected/clamped.
3. **Execution limits**: statement timeout set per query, `READ ONLY` transaction, result row cap.
4. **Output controls**: bare identifier columns (`customer_id`, `account_id`) are replaced with keyed HMAC pseudonyms (also when aliased or via `SELECT *`);
   rows whose count is below `min_group_size` (3) are suppressed when grouped by quasi-identifiers (segment, region, customer_since).
5. **Error hygiene**: the model only ever sees a sanitized stage/code/first-line message; clients never get stack traces or credentials.
6. **API**: optional `X-API-Key`, in-memory rate limit (also enforce at the gateway), query log without raw result data.

Retrieved context, examples and model SQL are treated as untrusted. `evals/text_to_sql/attacks.yaml` (34 attacks) must all be rejected; this runs in the normal test suite.
Limits: pseudonymisation is not anonymisation; synthetic data does not make disclosure risks disappear in a real deployment.
