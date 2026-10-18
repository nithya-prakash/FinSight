# Frontend (apps/web)

Next.js 16 + React 19 + TypeScript + Recharts. Eight views: Overview, Transactions, Alerts, Analytics, AI analyst, Investigations, Model ops, Operations.
Every number comes from an API call; each page has loading, error (with retry), empty and success states. Badges separate
**synthetic data**, **model predictions**, **ground-truth labels (synthetic)**, **database facts**, **agent hypotheses** and **analyst decisions**.
Browser code only calls same-origin `/api/*`, a server-side proxy (`app/api/[...path]/route.ts`) that adds `API_KEY` if configured, so no secret reaches the client.
Run: `docker compose up -d --build web api` -> http://localhost:3001. Tests: `cd apps/web && npm test` (Vitest + Testing Library, 9 tests) and `npx tsc --noEmit`.
Accessibility: labelled controls, skip link, keyboard-operable rows, `role=alert/status` states. Not covered: automated browser end-to-end tests.

Demo recording: `python scripts/record_demo.py` captures a walk-through of the running dashboard (Chrome via Playwright, frames stitched by ffmpeg) and writes `docs/demo.mp4` and `docs/demo.gif`. It never records an analyst decision.
