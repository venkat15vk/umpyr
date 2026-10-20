# Resolve v2 — implementation plan

Constraints: Haiku only at runtime, $5 total Haiku budget for the POC, synthetic data, one phase at a time with
a stop for go-ahead, no change to an existing demo flow without asking first.

## Phase 1 — Foundations (additive; demo flows unchanged) — **implemented 2026-10-20**

1. **Clause provenance and citations.** Every clause gains `document_version`, `section`, `page`, `span`,
   `text_hash`, `public`; backfilled for both sandboxes from the archived documents with no model calls
   (`umpyr sandbox provenance`). Rulings carry `citations[]`; the Ops view links rule chips to document, page
   and section. Agent-channel payload adds public citations.
2. **Case object and hash-chained ledger.** `resolve_cases` + `case_events` (append-only trigger). The runtime
   appends an event per stage with actor, action, basis, result and cost; a decision hash commits each turn.
   `GET /cases/{id}`, `/cases/{id}/events`, `/cases/{id}/verify`, `GET /sessions/{id}/case`.
3. **Cost per case.** Context-scoped cost meter; `llm_calls.case_id/session_id`; per-stage cost in events;
   `cost_usd` and `llm_calls` on the case; shown in the Ops governance panel.
4. **Typed escalations.** `data_gap`, `policy_gap`, `governance_gate` (and reserved `planner_disagreement`)
   on the case, in the ledger and in `ops.ruling.escalation`.
5. **Check hooks.** No-op provider; `check.ingest` and `check.pre_execution` events.
6. **Status events.** Stage → humane template map, emitted into `ops.status_events` (not yet shown to the
   customer; phase 3).
7. **Metrics.** `GET /workspaces/{id}/metrics`: cost per resolved case, escalation rate by type, time to
   resolution, cases by status.
8. **Source control.** GitHub remote, secret scan, baseline tag `v1-baseline`, phase commits.

Tests: ledger chain/verify/tamper/idempotency, provenance locate (exact, whitespace, section, page), runtime
events and citations, API routes. Spend: $0 (no model calls in tests or backfill).

## Phase 2 — Ingestion v2 and planning (alters extraction and the Ops view; **ask first**)

1. Extraction schema v2 (intents, party, timeline ask, reference kind, requested outcome) and delta extraction.
2. Structured-case embedding; historical planner (consensus sequence, similarity, agreement).
3. Live planner over the fixed action vocabulary; comparison by type and order; `planner_disagreement`.
4. Novelty thresholds → `data_gap`.
5. Ops view: plan panel (historical vs live, agreement).
Spend estimate: re-embedding 600 historical cases locally (no cost); ~1 extra Haiku call per turn (~$0.0005).

## Phase 3 — Customer experience (changes what customers see; **ask first**)

1. Status events rendered as interim customer messages.
2. Public citations in customer replies (contract of carriage, DOT rule); SOPs never shown.
3. Next-question prediction and proactive answers (refund timeline first).
4. Refund options menu with cash-first rule; customer choice logged.
5. Queued-customer experience (open item: pending message and/or approval SLA).

## Phase 4 — Execution and governance

1. Operations ontology: bindings per workspace; synthetic merchant system of record (orders, bookings,
   payment ledger) for both sandboxes.
2. Execution with idempotency keys and system-of-record confirmation.
3. Inline hard gate: duplicate refund, per-action caps, preconditions, dependency order; Check pre-execution.
4. Autonomy dial per action type and amount band; approval queue; decision card with citations;
   approve/reject labels.
5. Refund timeline prediction from synthetic settlement history; proactive follow-up event.

## Phase 5 — Policy lifecycle and learning

1. Section hashing, change detection, partial re-extraction, rule diff and versioning; rule version per
   historical case; step → rule mapping from history; cold-start retrieval.
2. Shadow-mode scorecards per binding, graduation thresholds, "last N tickets" replay artefact.
3. Learning-loop feeds (reopen, chargeback, settlement time) and the eval scoreboard (`eval_runs`).

## Open questions for the user

1. Queued-customer experience (phase 3, §11 of the design).
2. Switching Railway's deploy source to the GitHub repo (auto-deploy on push to main).
3. Go-ahead for phase 2, which changes the extraction prompt and adds a planner call per turn.
