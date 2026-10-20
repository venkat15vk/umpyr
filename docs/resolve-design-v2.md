# Umpyr Resolve — Design v2

Status: source of truth from 2026-10-20. Where V1 (`docs/ARCHITECTURE.md`, decisions D1–D39) differs, V1 changes.
Companion documents: `resolve-v2-gap-list.md` (V1 vs this design) and `resolve-v2-plan.md` (phases).

## 0. Scope and principles

Resolve handles customer disputes and enquiries forwarded by a merchant. V1 channel: merchant chatbot → Umpyr.
Personal agents (Muse) come later. Umpyr Check (ownership, claim verification, abuse) is deferred; this design
reserves two hooks for it (§16).

Running example: an Alaska customer, 3 passengers (2 adults, 1 child), $500 SEA→NYC, 3 h delay, wants to
cancel, get a refund, and know when the money lands.

Principles carried forward from V1 and kept:

1. **Rules decide, models assist.** Every decision is a deterministic evaluation of compiled rules over
   ontology facts. Language models extract, classify, draft and compare; they never decide or cite.
2. **Ontology first.** Rules, plans and bindings reference ontology field paths only.
3. **Everything cited.** Any policy-related decision shown anywhere carries a citation resolved from rule
   provenance (document, version, section, page, clause span, text hash). Citations are looked up, never
   generated.
4. **Everything audited.** One append-only, hash-chained event log per case; a final decision hash commits to it.
5. **PII masked on ingest.** Umpyr holds masked conversation text only.
6. **Haiku only at runtime, budget guarded.** Cost per call is recorded; cost per resolved case is a
   first-class metric.
7. **Synthetic data only** in every environment until a merchant signs.

## 1. System shape

```
merchant chatbot ──► Umpyr Resolve
                      │
   ingest ──► extract (schema, delta) ──► case state ──► plan ──► rules ──► governance gate ──► execute ──► notify
      │            │                        │            │          │              │              │           │
   Check hook   cost meter            case ledger    historical   rule pack    Check hook      ontology    status
   (§16)        (§1)                  (§2, §14)      + live       + regulation (§16)           binding     templates
                                                     planners     layer (§5)   caps, dupes     (§7, §11)   (§13)
                                                     (§3)                      (§9)
```

Every stage appends events to the case ledger (§14) and emits a status event (§13). Every stage that calls a
model records its cost against the case (§1).

## 2. Ingestion and structured extraction (§1 of the brief)

- The merchant forwards raw customer text plus context (channel, merchant id, optional record reference).
- Text is PII-masked before storage and before any model call (V1 `ingest/pii.py`, unchanged).
- A small model performs **schema-based extraction** (structured output, not keywords). Schema v2 adds to the
  V1 `claim`/`airline` sections:
  - `intents[]` — ordered list from a fixed vocabulary (`refund`, `cancel`, `rebook`, `status`, `timeline`,
    `compensation`, `complaint`, `bag`, `other`).
  - `reference` with `reference_kind` (`confirmation_code` | `order_number` | `none`).
  - `party`: `pax_count`, `pax_types[]` (`adult` | `child` | `infant`).
  - `amount_mentioned`, `requested_outcome` (remedy vocabulary), `timeline_ask` (bool).
  - `disruption`: `delay_minutes`, `cause`, `cancelled_by_airline`, `customer_declined_travel`.
- **Delta extraction.** From turn 2, the model receives the current case state plus the new message and returns
  only changed or newly learned fields. Code merges deltas; unchanged fields are never overwritten by `null`.
- **Cost meter.** A context-scoped meter attributes every model call to `(case_id, session_id, stage)`.
  `llm_calls` rows carry `case_id`/`session_id`; the case carries running `cost_usd` and `llm_calls`.
  Cost per resolved case is reported in `GET /workspaces/{id}/metrics`.

## 3. Case state and conversation persistence (§2)

- One **case** per conversation (`resolve_cases`), opened on the first message, linked 1:1 to the chat
  session. It holds the structured case state (`facts`, `intents`, `reference`, `category`, `plan`),
  `status`, typed `escalation`, cost, turn count, the ledger head hash and the final `decision_hash`.
- Each turn: delta extraction → merge → re-run plan → rules → gate → execute. Only changed fields update.
- **Executed-steps ledger** inside the case state: `executed[{step_key, idempotency_key, at, result_ref}]`.
  An action with an existing idempotency key never fires again across turns (`CaseLedger.ensure_once`).

## 4. Plan generation with online verification (§3)

- **Offline.** Historical tickets joined with system logs are mined into normalised step sequences per case
  type using the staff-action vocabulary (`learn/vocab.py`, 35 verbs) and procedure mining
  (`learn/procedures.py`). V1 keeps this.
- **Online, in parallel.**
  - *Historical planner*: embed the **structured case** (intents, category, disruption, party, requested
    outcome serialised in a fixed order), retrieve k nearest cases, aggregate their step sequences into a
    consensus plan (majority step set, median order), with mean similarity and agreement score.
  - *Live planner*: the model decomposes the intents into steps drawn from the fixed **action vocabulary**
    (§8), no free text.
- **Comparison** is by step type and order (sequence alignment on step kinds; count is ignored). History wins
  when the two agree or differ only in order of independent steps. Divergence raises a `planner_disagreement`
  governance flag (§7).
- **Novelty.** Mean neighbour similarity below `NOVELTY_MIN` or agreement below `AGREEMENT_MIN` marks the
  case novel → `data_gap` escalation path, plan still shown to the reviewer.

## 5. Next-question prediction (§4)

- An intent-sequence model (bigram over historical intent sequences per category, upgraded later) predicts the
  customer's likely next questions after the current intent, with probabilities.
- Used to **prefetch** (e.g. refund timeline, §12) and to **answer proactively** in the first reply when
  probability ≥ `PROACTIVE_MIN` and the answer is policy-safe (templated or cited).

## 6. Policy → rules (§5)

- **Compiler** (V1 `compile/`): the model segments policy documents into verbatim clauses and converts them
  to JSON Logic rules; one policy yields many rules; duplicates are removed by clause text hash.
- **Provenance on every clause**: `document_id`, `document_title`, `document_version` (sha256 of the fetched
  document, short form shown), `section` (nearest heading above the clause), `page` (PDF page, else null),
  `span` (`[start, end]` character offsets in the archived document text), `text_hash` (sha256 of the
  normalised clause text), `public` (true for documents customers may be shown: contract of carriage, public
  policy pages, regulations; false for internal SOPs and adopted practice).
- **Regulation layer.** Rules carry a tier; `law` outranks `contract` outranks `commitment` outranks
  `goodwill`. The engine evaluates in that order, so regulation overrides stricter merchant rules (e.g. US DOT
  refund on significant delay when the customer declines travel; card refunds within 7 business days).
- **Step → rule mapping** is learned from history: for each procedure step, the rules that governed it in the
  replayed historical cases. At runtime those rules are evaluated deterministically against case fields.
- **Policy drift.** Each document section is hashed. A scheduled check or merchant webhook re-fetches, diffs
  section hashes, re-extracts only changed sections, diffs rules and publishes a new pack version.
  History is not voided: step history is kept and only affected steps are re-bound to new rule versions.
  Every historical and live case stores the rule-pack version it was decided under.
- **Cold start.** A new merchant or policy without history falls back to retrieval over rules (nearest clause
  by embedding) plus escalation; the gap is tagged `policy_gap`.

## 7. Escalation triggers (§6)

Every escalation and every open ask carries one type, stored on the case and in the ledger, reported by type:

| type | raised when |
|---|---|
| `data_gap` | no close precedent (novel case), record not found after repeats, required fact missing |
| `policy_gap` | no rule pack, no rule matched, conflicting rules, cold-start fallback used |
| `planner_disagreement` | live planner and historical plan diverge by step type or order |
| `governance_gate` | PDP or execution gate blocks an irreversible step (cap, duplicate, precondition, mode) |

## 8. Ontology layer and execution (§7)

- The **data ontology** (V1 `packages/ontology`) stays: Customer, Order, Item, Shipment, Booking, Flight, Bag,
  Claim, Case, Facts; rules reference its field catalogue.
- The **operations ontology** (new) maps abstract steps to concrete merchant operations. Abstract step
  vocabulary: `lookup_record`, `verify_identity`, `cancel_itinerary`, `cancel_order`, `refund_party`,
  `issue_voucher`, `issue_points`, `rebook`, `issue_return_label`, `reship`, `request_evidence`, `notify`,
  `escalate`.
- A **binding** per merchant and step: `{step, endpoint_or_tool, request_schema, field_map (case-state path →
  request field), preconditions[], reversible: bool, depends_on[], confirm_via (system of record read)}`.
  Bindings are the single source of truth for ordering and governance (e.g. cancel before refund).
- New merchant = new bindings, not new reasoning logic. Sandboxes bind to a synthetic merchant system of
  record (orders/bookings table plus a synthetic payment ledger).

## 9. Automated onboarding (§8)

- The model generates bindings and rules from merchant API specs, tool definitions, policies and historical
  logs (V1 onboarding pipeline extended with a bindings step).
- **Shadow mode** runs the full pipeline on live traffic without executing; agreement with the human agent's
  action builds a per-binding **scorecard** (accuracy, amount error, volume).
- Bindings **graduate** to auto-execution past thresholds, with amount caps initially; the merchant admin
  approves "go live" from the scorecard.
- Sales artefact: "how we would have handled your last N tickets" = full-pipeline replay with decision cards.

## 10. Governance (§9)

- **Parallel monitoring**: the supervisor model checks every outbound reply against the ruling (V1).
- **Inline hard gate** before any irreversible action (refund, cancellation): duplicate-refund check against the
  case ledger and the system of record, per-action amount caps, binding preconditions, dependency order,
  autonomy-dial lookup (§11). The gate's verdict is an event; a block is a `governance_gate` escalation.
- The pre-execution **Check hook** runs inside the gate (§16).

## 11. Merchant control: autonomy dial (§10)

- Per action type and per amount band: `auto` or `queue` (e.g. auto-refund ≤ $200, queue above). Stored per
  workspace; the V1 per-session `auto_approve_limit` becomes the default band.
- Queued items show a **decision card**: plan, precedent cases, rules with citations, confidence, reasoning.
  Approve/reject are stored as training labels on the case ledger.
- Open item (ask before building): customer experience while queued — pending message and/or merchant
  approval SLA.

## 12. Refund options and execution (§11)

- Policy determines the allowed options; the customer chooses; the choice is logged as a ledger event.
- Options: original form of payment (cash), loyalty points, voucher/credit. Where regulation guarantees cash,
  cash is listed first and clearly; alternatives may be offered (optionally with a bonus) but never defaulted.
- Execution goes through the binding with an idempotency key `case_id:step_key`; success is confirmed by
  reading the merchant's payment system of record, not only the API response.

## 13. Refund timeline prediction (§12)

- Model on historical settlement data: payment method, network/issuer, processor, weekday → predicted window
  plus confidence. The regulatory bound (e.g. 7 business days for cards) is the outer limit.
- If the window passes without settlement, a proactive follow-up event fires.

## 14. Customer-facing status and citations (§13)

- Each pipeline stage emits a **status event**; a template map turns it into a humane message ("checking your
  booking", "reviewing refund options", "processing your refund"). Templated, never generated, so no policy or
  internal reasoning leaks. Honest notices for long waits and escalations.
- Citations:
  - Ops view: every rule chip links to the document and page with the clause highlighted (span).
  - Merchant decision card: citations per step.
  - Customer chat: public documents only (`public: true`); internal SOPs and adopted practice never shown.
- Citations come from rule provenance lookups (§6), never from the model.

## 15. Audit trail (§14)

- `case_events`: append-only, hash-chained per case from first contact. Each event:
  `seq`, `actor` (customer | umpyr | merchant_reviewer | merchant_system | check), `action` (message,
  extraction, classification, plan, rule_check, gate, option_offered, choice, approval, execution,
  notification, escalation, decision), `basis` (model and version, rule ids with cited clause and pack
  version, neighbour ids, confidence), `result`, `cost_usd`, `prev_hash`, `hash`.
  `hash = sha256(canonical({case_id, seq, prev_hash, actor, action, basis, result, at}))`.
- Database trigger forbids UPDATE and DELETE on `case_events`.
- **Decision hash**: `sha256(head_hash ‖ ruling_audit_hash)` written to the case on every decision; the latest
  is the final commitment. `GET /cases/{id}/verify` recomputes the chain.
- Conversation text is masked; Umpyr holds no raw customer data. Check events will prepend later.
- The same log is the merchant audit record, the chargeback evidence pack and the training feed.

## 16. Learning and feedback loop (§15)

- Signals: executed plan vs predicted plan; merchant approve/reject; escalations by type; downstream outcomes
  (reopen, chargeback after resolution — strongest negative label, actual settlement time).
- Updates: neighbour case store continuously; timeline and next-question models on schedule; scorecards drive
  queue → auto graduation.
- Metrics (`GET /workspaces/{id}/metrics`): plan accuracy vs history, escalation rate by type, refund error
  rate, chargeback-after-resolution rate, cost per resolved case, time to resolution.

## 17. Umpyr Check hooks (§16)

Two hook points with a no-op provider now (`CHECK_PROVIDER=none`):

- `check.on_ingest(workspace, case, masked_text, facts) → CheckSignal{status, risk, notes}` after masking,
  before extraction.
- `check.pre_execution(workspace, case, action, amount) → CheckVerdict{allow, status, reasons}` inside the
  governance gate, before any irreversible step.

Both results are ledger events, so the chain shape is stable when Check arrives.

## 18. Data model (additions to V1)

| table | purpose |
|---|---|
| `resolve_cases` | one per conversation: state, status, escalation, cost, head/decision hashes |
| `case_events` | append-only hash chain (trigger-protected) |
| `clauses` (+columns) | `document_version`, `section`, `page`, `span_start`, `span_end`, `text_hash`, `public` |
| `llm_calls` (+columns) | `case_id`, `session_id` |
| `bindings` (phase 4) | operations ontology per workspace |
| `autonomy_rules` (phase 4) | dial per action type and amount band |
| `executions` (phase 4) | idempotency key, binding, request, confirmation from system of record |
| `settlements` (phase 4) | synthetic settlement history for the timeline model |

## 19. Interfaces

- `POST /workspaces/{id}/sessions`, `POST /sessions/{id}/messages` (unchanged shape; `ops` gains `case`,
  `citations`, `escalation`, `status_events`, `cost`).
- `GET /sessions/{id}/case`, `GET /cases/{id}`, `GET /cases/{id}/events`, `GET /cases/{id}/verify`.
- `GET /workspaces/{id}/metrics`.
- Agent channel payload `umpyr-resolve/0.2` adds `citations` (public only) and `case_id`.
- Phase 4: `GET /workspaces/{id}/queue`, `POST /cases/{id}/decision` (approve | reject), bindings CRUD.

## 20. Infrastructure and source control

- Deployment unchanged: Cloudflare (DNS, Pages at demo.umpyr.org) and Railway (engine + Postgres).
  Existing tokens and setup are reused, never rotated by the agent.
- Code, design, prompts, synthetic generators and the eval harness live in the public GitHub repository
  `venkat15vk/umpyr`. No secret is ever committed (`.gitignore`, `.env.example` with names only); a history
  scan runs before every push. Synthetic data only.
- Railway deploys from the GitHub repo only after the user confirms switching the deploy source.
