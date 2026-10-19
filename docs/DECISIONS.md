# Decisions log

Short entries, newest last. Each: what we chose, why, and what it rules out.

## 2026-10-19 — M1

**D1. Haiku only, chosen by `CLAUDE_MODEL`.** Every runtime call uses the model in `.env`; the Claude provider refuses non-Haiku ids. Overrides the brief's "model name configurable" with a hard guard because the founder set a $5 total budget for the POC.

**D2. One LLM provider interface (`umpyr_engine.llm`).** `LLMRequest`/`LLMResponse` are provider-neutral; `LLM_PROVIDER=claude|opensource` selects the backend. `opensource` is a stub that raises. Keeps every prompt and every budget check in one place.

**D3. Budget guard in Postgres, UTC day.** `daily_spend` (one row per day) plus `llm_calls` (one row per call with step, tokens, cost). The guard checks before each call and records after; the API maps `BudgetExceeded` to HTTP 402 and the UI shows "Demo budget reached for today". Batch calls are priced at 50%. Unknown models fall back to the most expensive Haiku row so we never under-count.

**D4. Own JSON Logic evaluator instead of a PyPI package.** `json-logic`/`json-logic-qubit` are unmaintained and use floats. Ours is ~250 lines, uses `Decimal`, has no clock or randomness, and is the thing our determinism tests own. Rules reference ontology paths only; `Rule` validation rejects unknown paths.

**D5. Precedence model.** Rules carry `tier` (law > contract > commitment > goodwill) and `priority` within a tier (exceptions > defaults). Winner = first by (tier, -priority, rule_id). Two fired rules at the same tier+priority with different decision/remedy = conflict → `escalate`. No fired rule + missing required facts → `needs_fact`; no fired rule and nothing missing → `escalate` (we never invent a ruling).

**D6. PDP is separate from the ruling.** `authorize()` only answers "may this actor execute this action on this resource given this ruling". It enforces remedy→action licensing, amount ≤ ruled amount ≤ resource max, mode (autonomous/assist/shadow) and the auto-approve limit. Money above the limit → `needs_approval`.

**D7. Embedded Postgres for dev (`pgserver`).** This Mac has no Docker. `DATABASE_URL=pgserver://<dir>` starts PostgreSQL 16 + pgvector in-process via the `pgserver` wheel; `docker-compose.yml` remains the documented path for Docker users. Tests use an ephemeral pgserver dir so they need no services. Railway Postgres in prod (M5).

**D8. Postgres job queue, SSE for progress.** `jobs` + `job_events` tables; workers claim with `FOR UPDATE SKIP LOCKED`; the API streams `job_events` over SSE by polling every 0.5s. No Redis.

**D9. Resolve runtime will be a code-driven state machine (M3), not a free tool-use loop.** Classify → neighbours → procedure → lookup → extract → rule → authorize → draft are orchestrated in code; Haiku is called only inside judgment steps with structured outputs. Cheaper, deterministic, and matches "rules decide, models assist".

**D10. Prompt caching.** System prompts and tool definitions carry `cache_control`. Minimum cacheable prefix is 512 tokens on Haiku 5.5 but 4096 on Haiku 4.5; on 4.5 short prompts silently won't cache. We verify with `cache_read_input_tokens` in `llm_calls`.

**D11. PII masking is regex + name lists.** Emails, phones, Luhn-valid card numbers, street addresses, salutation/sign-off names, a common-first-name list, and the synthetic generator's own name list. Good enough for synthetic data; a real deployment would add an NER pass.

**D12. `.env` in `.env` only.** The root `.env` is read by pydantic-settings (engine) and by Vite (`envDir: ../..`, `VITE_*` only). Never read by tooling, never committed. The founder's `.env` sets a `DATABASE_URL` that currently fails to connect (see README "Database"); local work overrides it with `DATABASE_URL=pgserver://.pgdata` on the command line.
