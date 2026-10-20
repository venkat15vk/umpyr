# Resolve v2 — gap list (V1 as of commit d6db54d vs `resolve-design-v2.md`)

Legend: **Have** = present in V1 and kept; **Partial** = exists but short of the design; **Missing** = not in V1.
Phase numbers refer to `resolve-v2-plan.md`.

| # | Design area | V1 state | Gap | Phase |
|---|---|---|---|---|
| 1 | Schema-based extraction | **Partial**: structured output over `claim`/`airline` (`resolve/tools.py`), regex reference finder | No `intents[]`, `party` (pax count/types), `timeline_ask`, `requested_outcome` vocabulary, `reference_kind` | 2 |
| 1 | Cost per call / per case | **Partial**: `llm_calls` per call with cost; daily spend; no case link | `case_id`/`session_id` on calls, per-stage cost in the ledger, cost per resolved case metric | 1 |
| 2 | Case state object | **Partial**: `chat_sessions.state` JSONB blob | First-class `resolve_cases` row with status, escalation, cost, hashes | 1 |
| 2 | Delta extraction | **Missing**: full history re-extracted each turn | State + new message → delta; merge in code | 2 |
| 2 | Executed-steps ledger, idempotency | **Missing** (nothing executes) | `executed[]` with idempotency keys; `ensure_once` | 1 (ledger), 4 (execution) |
| 3 | Historical planner | **Partial**: k=3 neighbours over text embedding, display only; procedure chosen by category | Structured-case embedding, consensus step sequence, similarity/agreement scores | 2 |
| 3 | Live planner | **Missing** | Model decomposition into fixed action vocabulary | 2 |
| 3 | Plan comparison, divergence flag, novelty | **Missing** | Type-and-order comparison, `planner_disagreement`, novelty thresholds | 2 |
| 4 | Next-question prediction, prefetch, proactive answer | **Missing** | Intent-sequence model over history; proactive templated answers | 3 |
| 5 | Policy compiler | **Have**: segment → clauses → JSON Logic rules, overrides, lint, conflicts | — | — |
| 5 | Rule provenance | **Partial**: clause id, verbatim text, source URL, whole-document sha256 | `document_version`, `section`, `page`, `span`, `text_hash`, `public` on every clause | 1 |
| 5 | Regulation layer | **Have**: tier precedence law > contract > commitment > goodwill | — | — |
| 5 | Step → rule mapping from history | **Partial**: procedure steps carry model-assigned `clause_ids` | Learn from replayed history; store per step | 5 |
| 5 | Policy drift (document) | **Missing**: only policy-vs-practice drift | Section hashes, change detection, partial re-extraction, rule diff, versioning | 5 |
| 5 | Rule version per historical case | **Missing** (live rulings store it; mined cases do not) | Store pack version on `cases.record.policy` | 5 |
| 5 | Cold-start fallback retrieval | **Missing** (D11: compiled, not searched) | Nearest-clause retrieval + `policy_gap` escalation | 5 |
| 6 | Typed escalation triggers | **Missing**: free-text reasons | `escalation.type` on case and ledger; metrics by type | 1 |
| 7 | Data ontology | **Have** | — | — |
| 7 | Operations ontology (bindings) | **Missing** | Bindings with endpoint, schema, field map, preconditions, reversibility, dependencies | 4 |
| 8 | Onboarding pipeline | **Have**: ingest → compile → mine → taxonomy → router → procedures → drift | Bindings step from API specs | 4 |
| 8 | Shadow mode, scorecards, graduation | **Partial**: PDP `shadow` mode per session | Per-binding scorecards, thresholds, go-live approval | 5 |
| 8 | "Last N tickets" replay artefact | **Partial**: drift replay of rules only | Full-pipeline replay with decision cards | 5 |
| 9 | Governance parallel monitoring | **Have**: supervisor on replies | — | — |
| 9 | Inline hard gate | **Partial**: PDP checks role, licence, amount ≤ ruled, mode, limit | Duplicate-refund, per-action caps, preconditions, execution-time gate | 4 |
| 10 | Autonomy dial per action/amount | **Partial**: one `auto_approve_limit` per session | Per action type and amount band, per workspace | 4 |
| 10 | Approval queue, decision card, labels | **Missing**: `pending_approval` is only a status | Queue endpoints, card payload, approve/reject stored as labels | 4 |
| 10 | Queued customer experience | **Open item** | Ask before building | 3 |
| 11 | Refund options menu, choice logged | **Missing**: single remedy from winning rule | Options from policy, cash-first rule, choice event | 3 |
| 11 | Execution with idempotency, SoR confirmation | **Missing** | Synthetic system of record, executions table | 4 |
| 12 | Refund timeline prediction | **Missing** | Settlement model, regulatory bound, follow-up event | 4 |
| 13 | Status events → templated customer messages | **Partial**: templated fallbacks per status only | Stage events with template map; shown to customer | 1 (events), 3 (customer) |
| 13 | Citations in ops view | **Partial**: clause ids with hover text | Chips link to document/page/section with span | 1 |
| 13 | Citations on decision card | **Missing** | With phase 4 card | 4 |
| 13 | Customer citations, public docs only | **Missing**: no public flag | `public` flag (1); customer reply citations (3) | 1, 3 |
| 14 | Hash-chained append-only ledger | **Partial**: one hashed `rulings` row per turn, no chain | `case_events` chain, trigger, verify endpoint, decision hash | 1 |
| 15 | Learning signals and metrics | **Partial**: drift triage, router retrain, unused `eval_runs` | Metrics endpoint (1), label capture (4), scoreboard and outcome feeds (5) | 1, 4, 5 |
| 16 | Check hooks | **Missing** | No-op provider at ingest and pre-execution, events logged | 1 |
| — | Source control | **Missing**: local git only | GitHub `venkat15vk/umpyr`, secret scan before push | 1 |
