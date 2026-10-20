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

## Upload flow (M4)

```bash
make api && make worker && make web      # three terminals
open http://localhost:5173/try
```

1. **/try** creates a workspace (`POST /workspaces`), deleted automatically after `DATA_RETENTION_DAYS` (14).
2. **/<id>/onboard**: upload tickets (CSV, XLSX, JSON, JSONL, .eml, .mbox; max 2,000 tickets / 20 MB), policies
   (PDF, DOCX, Markdown, HTML, or a help-center URL crawled same-domain two levels deep) and optional orders (CSV).
   The **column-mapping agent** proposes how your columns map to Umpyr's ticket schema with confidence and sample
   values; you approve or correct it before anything is ingested. "Use Rockler sample files" loads the synthetic
   export (`samples/rockler/tickets.csv`, `orders.csv`, policy text) for a one-click demo.
3. **Start onboarding** enqueues `onboard.pipeline` on the worker; progress streams over SSE:
   ingest (PII masked) → compile → mine → taxonomy → router → procedures → drift.
4. **/<id>/setup**: approve, rename or merge categories; approve procedures; enable/disable rules (each shows its
   clause text and source); triage drift findings (policy is right / update policy / adopt as rule / ignore).
   The chat opens once at least one category and procedure are approved (`GET /workspaces/{id}/readiness`).
5. **/<id>** is the same resolve chat as the sandboxes.

Uploads are stored on local disk under `services/engine/data/uploads/` (git-ignored) behind a storage interface;
R2 with presigned URLs is wired in M5. `umpyr retention` (or the worker, hourly) deletes expired upload workspaces
and their files.

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
- [x] **M4 — Upload flow**: `/try`, column mapping, live onboarding, setup/approve.
- [x] **V2 phase 1 — foundations** (2026-10-20): clause provenance + citations, case object + hash-chained ledger, cost per case, typed escalations, Check hooks, status events, metrics. Design: `docs/resolve-design-v2.md`; gaps: `docs/resolve-v2-gap-list.md`; plan: `docs/resolve-v2-plan.md`.
- [x] **V2 phase 2 — extraction v2 + planners** (2026-10-20): intents/party/timeline extraction with delta mode, historical consensus plan vs live Haiku plan compared by step type and order, novelty flag, planner-disagreement gate on remedies, plan panel in the Ops view (D43–D45).
- [x] **V2 phase 3 — customer experience** (2026-10-20): templated status strip, public-document citations under replies, predicted next questions with a cited refund-timeline answer, refund options (cash first where regulated) with the choice re-binding the ruling, queued-customer SLA with honest overdue escalation (D46–D49).
- [~] **M5 — Eval scoreboard + deploy**. Deployed 2026-10-20 at https://demo.umpyr.org/alaska and /rockler (password gate, Railway engine, $1/day public Haiku cap; see `infra/DEPLOY.md`, D39). Eval scoreboard still open.

> **POC note:** any order number or confirmation code typed in the chat is treated as real. The engine clones the record behind the nearest historical case under that reference (PII stripped, flight state aligned with what the customer said) and marks it `stand_in` in the ops panel. Set `ASSUME_REFERENCE_EXISTS=false` to get the strict behaviour (unmatched reference quoted back, hand-off to a person on repeat).

