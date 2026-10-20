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

## 2026-10-19 — M2

**D13. Policy sources: archive raw + text + meta; Alaska via Next.js payload and PDF.** Alaska's pages render client-side, but their CMS content is embedded in the page's `__NEXT_DATA__` JSON, so we extract text from that without a browser (Playwright does not support this macOS version, and headless Chrome only rendered the cookie banner). The contract of carriage `view-all` URL redirects to the official PDF, which we archive and extract with PyMuPDF. 14 CFR Part 260 comes from the eCFR versioner API (XML). Rockler's `/return-policy` redirects to the FAQ page; both ids are archived, duplicates are skipped at compile time by SHA-256.

**D14. Alaska contract of carriage compiled only for Rules 1, 7, 8, 15, 17.** The other rules (children, animals, codeshare, fares, data) do not govern in-scope disputes. Configured per source as `keep_sections` in `samples/alaska/sources.json`.

**D15. Compiler = segment (verbatim clauses) -> convert (typed rules) -> lint -> assemble, with a merchant-approval step.** Haiku over-marks relevance and emits some unconditional or no-op rules, so code rejects rules with `when: true` outcomes, approve-with-no-remedy, and unknown ontology paths (one repair attempt with the validation error). JSON Logic is passed as a JSON-encoded string because structured outputs need concrete types. Potential conflicts are listed only when two rules share tier, priority, a claim reason and at least one fact field; the engine still escalates real conflicts at runtime.

**D16. Sandbox curation lives in `samples/<client>/rule_overrides.json`.** This stands in for the merchant approval UI (M4): `disable`, `enable_only`, `patch` and `add`. Added rules are ids ending `.mN`, each cites an existing clause and carries a "merchant-approved reading" description. Rockler: 22 compiled -> 16 approved. Alaska: 155 compiled -> 37 approved. Raw compiler output is cached in `rules_raw.json` so re-curation never re-spends.

**D17. Synthetic data is scenario-driven, not free-form.** Code generates orders/bookings, picks a scenario template, builds ontology facts, asks the rule engine for the policy ruling, derives a staff outcome (85% consistent, 15% labelled drift), and fixes the thread's turn plan and staff actions. Haiku writes only the words. Hidden ground truth (`tickets_truth.jsonl`) lets us score mining, taxonomy and drift detection. Fake PII uses example.com domains and 555 numbers; the names used are saved so masking is verifiable.

**D18. needs_fact asks only for the highest-precedence undecidable rules' facts.** Otherwise every applicable rule's requirements would be demanded at once.

**D19. Local hash embeddings when VOYAGE_API_KEY is absent.** `EMBEDDING_PROVIDER=auto` picks Voyage when the key exists, else a deterministic 1024-d HashingVectorizer. Clustering quality is lexical, not semantic, until the key is added; the pipeline and schema are identical.

**D20. ruff ignores E501.** `ruff format` wraps code; long literal strings and prompts are fine.

**D21. `LLM_BATCH=false` runs offline jobs synchronously with 6 concurrent requests.** The Batch API halves the price but queued 10 to 25 minutes per step today; the founder chose speed for the rest of M2. Default stays batch.

**D22. Drift comparison semantics.** `refund`/`partial_refund`/`cancel_order` and `store_credit`/`travel_credit`/`voucher` are treated as the same money movement; an informational ruling (approve/none) is never drift; `needs_approval`/`escalate` are never drift; staff acting on a `needs_fact` ruling counts as drift only when the missing fact is evidence (photos, receipt, bag report). Scored against the hidden truth: Rockler precision/recall 0.95/0.95, Alaska 0.71/0.91.

**D23. Cost of iteration.** M2 spent $1.44 against a $0.78 estimate: mining, synth and eval each ran 2 to 4 times while bugs were fixed (generator cap at 300, un-curated pack used for the first threads, photo-less drift undetectable), and the final runs were synchronous at full price. Cached outputs mean a fresh clone rebuilds both sandboxes for $0.

## 2026-10-19 — M3

**D24. Runtime is a fixed state machine, not a tool-use loop.** `resolve/runtime.py` calls the tools in a fixed order every turn; Haiku is used for classification fallback, fact extraction, reply drafting and the supervisor check only. Rulings come from `umpyr_rules.evaluate`; actions from `umpyr_rules.authorize`. The brief's tool names survive as the ops-view trace.

**D25. No ruling without the merchant record.** If no order/booking matches, the turn returns `needs_fact` asking only for the reference. This prevents rulings on claim facts alone.

**D26. Customer text is masked before storage and before any model call.** Replies therefore address the customer generically; the merchant's own system can personalise on send.

**D27. Supervisor failure falls back to a templated reply.** The draft is regenerated once with the supervisor's issues; if it still fails, a deterministic template built from the ruling is sent and the governance strip shows the failed check.

**D28. Auto-approve limit applies to any action with a value**, including replacements, not only cash movements.

**D29. Cases embed the customer's opening message**, so the router and neighbour search operate in the same text space as live messages. Eval scenarios use a distinct record id range (R-2xxxxx) and their records are loaded alongside the synthetic orders.

## 2026-10-19 — M4

**D30. Uploads go through the API to local disk in dev.** The brief's R2 presigned URLs need an R2 bucket and keys the founder must create; `storage.py` keeps the interface (`put/get/delete_prefix`) so R2 is a drop-in in M5. Per-workspace caps: 2,000 tickets, 20 MB.

**D31. Column mapping is proposed by Haiku and approved by a human.** The agent sees headers and five sample rows and returns target→source with confidence; thread format (JSON list / plain text / split customer+staff columns) is detected and editable. Nothing is ingested until the mapping is approved. Mail files (.eml/.mbox) skip mapping: threads are grouped by normalised subject and roles are inferred from staff domains or alternation.

**D32. Uploaded workspaces compile without curation and start in review.** The compiler output goes live as-is (no `rule_overrides.json`); categories and procedures are created pending/draft; the setup page is the approval step and the chat is gated on it. Rule toggles rewrite the active pack so they take effect immediately.

**D33. Live onboarding runs synchronously inside one worker job.** Batch latency would make "minutes, not an FDE engagement" feel like hours; sync Haiku on 300 tickets takes about three minutes and under $0.40.

**D34. Retention is a hard delete.** The sweeper deletes the workspace row (cascades through every tenant table) and its stored files once `expires_at` passes; sandboxes never expire.

**D35. The dev API runs without auto-reload during demos.** Uvicorn's reloader hung after a burst of file changes; `ENGINE_ENV=prod` disables it.

**D36. Unmatched references are named, never sticky, and hand off after a repeat.** A reference that matches no record is quoted back ("XVZLT did not match any record; please double-check it") instead of re-asking for "the confirmation code". A missed reference is never kept in session state, so the customer's correction is read on the next turn. If the customer confirms the same bad reference again, or three different ones miss, the turn escalates to a person who can locate the record by name and date. Bare 6-character tokens count as codes only when typed in upper case; keyword-prefixed codes accept 5-8 characters. Why: the first Alaska demo looped three times asking for a code the customer had already sent (2026-10-19).

**D37. References given in chat are assumed to exist (POC).** `ASSUME_REFERENCE_EXISTS=true` (default) makes the runtime clone the record behind the nearest historical case under any unknown reference, strip the template's customer PII, align its flight state with what the customer described (delay minutes, cancellation, cause), and persist it so later turns reuse it. The ops panel marks the record `stand_in` and the lookup step says so. With the flag off, D36 applies (quote the unmatched reference, escalate on repeat). Why: the user asked on 2026-10-19 that the demo never stall on an unknown confirmation code.

**D38. Public demo = Pages password gate + proxied API, nothing else exposed.** A shared password (Pages secret) sets a signed 7-day cookie; the same middleware proxies `/api/*` to Railway and stamps `x-umpyr-gateway`, which the engine requires on every route except `/health` whenever `API_GATEWAY_SECRET` is set. The public build (`VITE_PUBLIC_DEMO=true`) compiles out `/try`, onboarding and setup, so no stranger can start a paid job; the worker is not deployed. Seeding is `umpyr sandbox seed`, which loads both merchants from `samples/` with the daily cap forced to 0 so it can never call the LLM. Why: the user wanted www.umpyr.org/alaska and /rockler behind one password, deployed by themselves from a runbook, without giving the agent Cloudflare or Railway credentials (2026-10-19).

**D39. The demo lives at demo.umpyr.org; the landing page on www stays.** Deployed 2026-10-20 by `PUBLIC_HOST=demo.umpyr.org make bootstrap`: Railway project `umpyr` (Postgres + `umpyr-engine`, public daily Haiku cap $1 via `DAILY_COST_CAP_USD_PROD`), Cloudflare Pages project `umpyr-web` with the custom domain and a proxied CNAME in the existing zone. Two things learned: Railway ignores `railway.json` on a CLI upload, so the service variable `RAILWAY_DOCKERFILE_PATH=infra/Dockerfile` is what makes it build the Dockerfile; and Railway's CLI only accepts account tokens, so a workspace token in `.env` is detected and the script falls back to the `railway login` session (started inside Claude Code with `script -q /dev/null railway login --browserless`). The chosen host is remembered in `infra/.deploy.state` so plain `make deploy` keeps it. Why: the existing `umpyr` Pages project already serves a landing page on www and the user had not asked to replace it.

**D40. Design v2 is the source of truth; V1 changes to match it.** `docs/resolve-design-v2.md` consolidates the user's 2026-10-20 design (ingestion, case state, dual planners, next-question prediction, policy provenance and drift, typed escalations, operations ontology, automated onboarding, governance gate, autonomy dial, refund options, timeline prediction, status templates, hash-chained audit, learning loop, Check hooks). `resolve-v2-gap-list.md` records where V1 falls short and `resolve-v2-plan.md` phases the work. Phase 1 is foundations only and alters no existing demo flow; phases that change what customers see or how extraction runs wait for explicit go-ahead.

**D41. Citations are looked up from clause provenance, never generated.** Every clause now carries `document_id`, `document_version` (sha256 of the fetched document), `section` (nearest heading), `page` (PDF), `span` (character offsets in the archived text), `text_hash` and `public`. Provenance is computed by locating the verbatim clause in the archived document (`compile/provenance.py`: exact, whitespace-insensitive, then first-words anchor; curly quotes folded), with no model calls; `umpyr sandbox provenance` backfills the two sandboxes and `umpyr sandbox seed` backfills at boot when rows lack a hash. Rulings carry `citations[]`, the Ops view links rule chips to document, section and page, and the agent-channel payload (`umpyr-resolve/0.2`) carries public citations only. Clauses the locator cannot find keep `located: false` and cite the document alone (5 of 600 in the sandboxes: a tariff table and paraphrased lines).

**D42. One case per conversation with an append-only, hash-chained ledger.** `resolve_cases` holds the case; `case_events` holds one event per pipeline stage per turn (actor, action, basis, result, cost, `prev_hash`, `hash`), a Postgres trigger refuses UPDATE and DELETE (retention cascade excepted), and `decision_hash = sha256(head_hash || ruling_audit_hash)` commits each decision. Cost is attributed per case by a context-scoped meter the LLM provider reports into (`llm_calls.case_id` too), so cost per resolved case is a real metric (`GET /workspaces/{id}/metrics`). Escalations are typed (`data_gap`, `policy_gap`, `governance_gate`, reserved `planner_disagreement`), Check has no-op hooks at ingest and pre-execution so the chain shape is final, and status events are templated per stage (shown in the Ops view only until phase 3). The V1 `rulings` row is kept as the per-turn audit record and is what the decision hash commits to.
