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

## Tests

```bash
make test           # pytest (ontology, rules, engine) + web typecheck + vitest
make lint           # ruff
```

Engine tests start an ephemeral embedded Postgres; no services or API keys needed (LLM calls are mocked).

## Milestones

- [x] **M1 — Skeleton**: ontology, rule engine + PDP with tests, schema + migrations, FastAPI health/spend/jobs, React shell, budget guard.
- [ ] **M2 — Sandboxes**: policy fetch + compile, synthetic data, offline onboarding for Rockler and Alaska.
- [ ] **M3 — Resolve chat**: `/rockler`, `/alaska` with ops view, toggles, audit.
- [ ] **M4 — Upload flow**: `/try`, column mapping, live onboarding, setup/approve.
- [ ] **M5 — Eval scoreboard + deploy**.
