# Umpyr Resolve — POC

Resolves customer disputes with an approved procedure, compiled policy clauses and a deterministic rule engine. See `UMPYR_POC_BRIEF.md` for the spec, `docs/ARCHITECTURE.md` for the design and `docs/DECISIONS.md` for the log.

All demo data is synthetic. Public policy sources are archived with URL and fetch date.

## Prerequisites

- Python 3.12 via [uv](https://docs.astral.sh/uv/) (`uv python install 3.12`)
- Node 20+ and npm
- Postgres 16 + pgvector: either Docker (`docker compose up -d db`) **or** nothing at all — the default `DATABASE_URL=pgserver://.pgdata` runs an embedded Postgres via the `pgserver` package.
- A root `.env` (copy `.env.example`). Required: `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` (a Haiku id), `DAILY_COST_CAP_USD`, `LLM_PROVIDER=claude`. Optional: `VOYAGE_API_KEY` (M2+), `DATABASE_URL`, `VITE_API_BASE_URL`.

## Setup

```bash
make setup          # uv sync + npm install
make db             # alembic upgrade head (creates the pgvector extension)
```

### Database

If your `.env` sets a `DATABASE_URL` that is not reachable from this machine, override it per command:

```bash
DATABASE_URL=pgserver://.pgdata make db
DATABASE_URL=pgserver://.pgdata make api
```

## Run (M1)

```bash
make api            # http://localhost:8000  (GET /health, /admin/spend, /workspaces, POST /jobs/demo, GET /jobs/{id}/events)
make worker         # background worker (runs queued jobs)
make web            # http://localhost:5173
```

Budget: every Haiku call is priced and summed per UTC day in Postgres. At `DAILY_COST_CAP_USD` the API returns 402 and the UI shows "Demo budget reached for today". `make spend` prints today's spend by pipeline step; `make smoke` makes one tiny call (< $0.001) to verify the key and accounting.

## Sandboxes (M2)

Two sandbox merchants, Rockler and Alaska Airlines, are built offline from their public policies plus synthetic
tickets. Everything is cached under `samples/<client>/`, so a fresh clone can load the sandboxes without any LLM spend:

```bash
cd services/engine
uv run umpyr sandbox build rockler      # uses cached outputs; add --rebuild to re-run the LLM steps
uv run umpyr sandbox build alaska
```

Steps and artefacts (`samples/<client>/`):

| step | what it does | output |
|---|---|---|
| fetch | archive public policy sources (raw + text + meta with URL, SHA-256, fetch date) | `policy/*.{html,pdf,xml,txt,meta.json}` |
| compile | Haiku segments documents into verbatim clauses, converts dispute-relevant ones to JSON Logic rules; code lints and assembles a versioned rule pack; `rule_overrides.json` holds the merchant's approvals/edits | `clauses.json`, `rules_raw.json`, `rule_pack.json`, `compile_report.json` |
| synth | scenario-driven synthetic orders/bookings + ~300 tickets with hidden ground truth (15% labelled drift) | `orders.json`/`bookings.json`, `tickets.jsonl`, `tickets.csv`, `tickets_truth.jsonl` |
| ingest | PII-mask and load tickets + merchant records into Postgres | DB |
| mine | Haiku extracts a structured case (claim facts, staff actions, outcome) per ticket; joined with the order record into ontology facts | `cases.jsonl` |
| taxonomy | embed -> HDBSCAN (k-means fallback) -> Haiku names/merges clusters; pain score = volume x avg hours | `taxonomy.json` |
| router | calibrated logistic regression on embeddings; <0.70 confidence falls back to the LLM at runtime | `router_metrics.json` |
| procedures | process-mine staff action sequences (steps in >=60% of cases) -> Haiku drafts a procedure bound to clauses -> auto-approved (sandbox) | `procedures.json` |
| drift | replay the rule pack over mined cases: drift findings, unwritten-rule candidates, gold train/test split; scored against the hidden truth | `drift_report.json` |
| eval | ~1,000 scenarios per client from the compiled rules (facts + expected ruling + Haiku-written customer message) | `eval_scenarios.jsonl`, `eval_report.json` |

Individual steps: `uv run umpyr sandbox fetch|compile|synth <client>` and `build --steps ingest mine ...`.
Results are visible through the API: `GET /workspaces/{id}/summary|categories|procedures|rules|drift|cases|policy-documents`.

Embeddings use Voyage when `VOYAGE_API_KEY` is set; otherwise a local hash embedder keeps the pipeline runnable (lexical quality only).

## Resolve chat (M3)

```bash
make api      # http://localhost:8000
make web      # http://localhost:5173  ->  /rockler  or  /alaska
```

Each customer message runs one turn of the resolve runtime: classify (router, LLM fallback below 0.70) -> neighbour
cases (pgvector) -> approved procedure -> record lookup -> fact extraction -> **rule engine** -> **PDP** -> reply draft
-> supervisor check -> audit record (SHA-256). The ops view shows every step; the governance strip shows the pinned
policy version, procedure@version, authority, supervisor result and audit hash.

- Toggles: mode (Autonomous / Assist / Shadow), reply channel (Email / Customer's AI agent, which renders a
  `umpyr-resolve/0.1` JSON message), auto-approve limit.
- Merchant chat tab answers questions about the merchant's own data from computed aggregates.
- API: `POST /workspaces/{id}/sessions`, `POST /sessions/{id}/messages`, `PATCH /sessions/{id}`,
  `POST /workspaces/{id}/merchant-chat`, `GET /workspaces/{id}/audit`, `GET /workspaces/{id}/examples`.
- Cost: about $0.001 per turn (3-4 Haiku calls). At the daily cap the API returns 402 and the UI shows
  "Demo budget reached for today".

Sandbox clock: synthetic data is dated relative to 2026-10-01 (`workspace.settings.as_of`), so day counts in rulings
match the scenarios.

## Tests

```bash
make test           # pytest (ontology, rules, engine) + web typecheck + vitest
make lint           # ruff
```

Engine tests start an ephemeral embedded Postgres; no services or API keys needed (LLM calls are mocked).

## Milestones

- [x] **M1 — Skeleton**: ontology, rule engine + PDP with tests, schema + migrations, FastAPI health/spend/jobs, React shell, budget guard.
- [x] **M2 — Sandboxes**: policy fetch + compile, synthetic data, offline onboarding for Rockler and Alaska.
- [x] **M3 — Resolve chat**: `/rockler`, `/alaska` with ops view, toggles, audit.
- [ ] **M4 — Upload flow**: `/try`, column mapping, live onboarding, setup/approve.
- [ ] **M5 — Eval scoreboard + deploy**.
