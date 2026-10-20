> Superseded for new work by `resolve-design-v2.md` (2026-10-20). This file describes the V1 POC as built; phase 1 of v2 added clause provenance, the case ledger (`resolve_cases`, `case_events`), per-case cost, typed escalations and Check hooks on top of it.

# Umpyr Resolve — architecture (POC)

## Shape

```
apps/web            React + Vite + TS   (Cloudflare Pages, M5)      /try  /<client>  /<client>/onboard  /<client>/setup
services/engine     FastAPI + worker    (Railway, M5)               /health /admin/spend /jobs /workspaces ... (M3+: /resolve, /onboard)
packages/ontology   Pydantic models + field catalog + JSON Schema export
packages/rules      JSON Logic evaluator, RulePack, precedence engine, PDP, audit hash
samples/<client>    policy sources (raw + text), synthetic tickets/orders, eval scenarios (M2)
```

Postgres (+pgvector) is the only stateful service. The worker and the API share it: jobs, job events, LLM spend, all tenant data.

## Principles → mechanisms

| Principle | Mechanism |
|---|---|
| Rules decide, models assist | `umpyr_rules.evaluate(pack, facts)` is pure and deterministic. Haiku only classifies, extracts facts, drafts text, and (at onboarding) proposes clauses/rules/procedures. |
| Policy is compiled, not searched | Policy docs → `Clause` (id, verbatim text, URL, tier) → `Rule` (JSON Logic over ontology paths + effect) → versioned `RulePack`. Embeddings are used for neighbours and clustering only. |
| Agents pick procedures | Procedures are rows with `status=approved`; the runtime selects by category and pins `category@version` into the audit record. |
| Every action passes a PDP | `umpyr_rules.authorize(actor, action, resource, facts, ruling, policy)` → allow / deny / needs_approval with clause ids. |
| Ontology-first | `umpyr_ontology.FIELD_CATALOG` is derived from the models; `Rule` validation rejects any other path. |
| Audit everything | `rulings` rows store ticket, procedure@version, pack version, facts, checks, decision, and `audit_hash` = SHA-256 of canonical JSON. |
| PII masked on ingest | `umpyr_engine.ingest.pii.PIIMasker` runs before any insert; only masked text is stored. |
| Learning from history | Drift (staff outcome ≠ policy outcome) is stored in `drift_findings`, never used as a label; gold = policy-consistent cases. |

## LLM layer

`umpyr_engine.llm`:
- `LLMRequest` → provider → `LLMResponse`. Provider chosen by `LLM_PROVIDER` (`claude` implemented, `opensource` stub).
- `ClaudeProvider`: model from `CLAUDE_MODEL` (must be Haiku), `cache_control` on system + last tool, `output_config.effort` (default `low`), structured output via `output_config.format`, Batch API via `batch()`.
- `BudgetGuard`: `check()` before every call, `record()` after; `daily_spend` per UTC day; cap from `DAILY_COST_CAP_USD`; price table in `llm/pricing.py`.
- Embeddings (`umpyr_engine.embeddings`, Voyage) log to `embedding_calls` and are not capped.

## Data model (see `services/engine/umpyr_engine/db/models.py`)

workspaces · uploads · tickets · orders · cases (pgvector embedding) · categories · procedures · policy_documents · clauses · rule_packs · rules · drift_findings · eval_scenarios · eval_runs · router_models · chat_sessions · chat_messages · rulings · jobs · job_events · llm_calls · daily_spend · embedding_calls

Every tenant table has `workspace_id`; queries filter by it. Uploaded workspaces get `expires_at` (14 days default) and a sweeper job (M4).

## Onboarding pipeline (M2, offline for the sandboxes; worker jobs `onboard.*` for uploads in M4)

```
sources.json ──fetch──► policy/*.txt ──compile──► clauses.json ──► rules_raw.json ──(rule_overrides.json = merchant approval)──► rule_pack.json ──► DB
rule_pack ──synth──► orders.json + tickets.jsonl (+ hidden tickets_truth.jsonl)
tickets ──ingest (PII mask)──► tickets/orders tables ──mine (Haiku, batch)──► cases (facts, staff actions, outcome)
cases ──taxonomy (embed, HDBSCAN, Haiku names)──► categories ──router (logreg)──► router_models
categories + cases + rule pack ──procedures (process mining + Haiku draft)──► procedures (approved)
cases + rule pack ──drift (replay)──► drift_findings, gold split ──eval──► eval_scenarios
```

Every LLM step runs on the Batch API and caches its output under `samples/<client>/`; `--rebuild` re-spends.

## Upload flow (M4)

```
/try ──POST /workspaces──► workspace(kind=upload, expires_at=+14d)
/<id>/onboard ──uploads──► storage (local disk; R2 in prod) ──parse──► mapping agent (Haiku) ──approve──► uploads.mapping
            ──policy URL/file──► policy_documents
            ──Start──► job onboard.pipeline ──SSE──► ingest → compile → mine → taxonomy → router → procedures → drift
/<id>/setup ──approve/merge/toggle──► categories, procedures, rules, drift_findings ──readiness──► /<id> chat
worker (hourly) ──retention.sweep──► delete expired upload workspaces + files
```

## Runtime flow (M3)

```
customer message ──► classify (router; LLM fallback <0.70) ──► find_neighbours (pgvector, drift flags)
   ──► get_procedure (approved, pinned version) ──► lookup_order (orders table) ──► extract_facts (Haiku, structured)
   ──► rule (deterministic) ──► authorize (PDP) ──► draft_reply (Haiku) ──► supervisor check (Haiku) ──► audit record
```
`needs_fact` loops back to the customer with a question; `needs_approval`/`escalate` stop at a human.
