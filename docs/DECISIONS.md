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
