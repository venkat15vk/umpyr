# Umpyr Resolve — POC Build Brief

You are building the proof of concept for **Umpyr**, a startup I'm founding (I'm the CEO). Work in this repo: `/Users/vk/Desktop/Startup/umpyr`. Read this whole brief, then produce a short plan with milestones before writing code. Stop and check with me at the end of each milestone.

## 1. What Umpyr is

Umpyr resolves customer disputes and order queries for merchants. A ticket comes in, an AI agent classifies it, picks an **approved procedure**, binds the merchant's **policy clauses** and **order data** to each step, runs **deterministic checks**, and issues a **ruling** (decision, remedy, amount, fault) with a full audit trail. It works for human customers today and for customers' AI agents (e.g. Meta Muse) tomorrow — same engine, different channel adapter. Tagline: "No help desk required."

Two products share one ontology: **Umpyr Check** (pre-payment risk/intent — out of scope here) and **Umpyr Resolve** (disputes — this POC).

## 2. What the POC must prove

1. **Onboarding takes minutes, not an FDE engagement.** A merchant uploads a ticket export and policy documents (or a help-center URL); Umpyr learns categories, procedures and rules from them and shows its work.
2. **Resolution after onboarding is correct and explainable.** A chat at `umpyr.org/<client>` takes a customer complaint and returns a ruling, with an ops view showing every step.

Two pilot merchants: **Rockler** (woodworking retailer) and **Alaska Airlines**. Use synthetic tickets plus their real **public** policies. No real customer data.

## 3. Non-negotiable design principles

- **Rules decide, models assist.** LLMs and ML classify, extract facts, retrieve, draft procedures and write replies. The **ruling** comes from a deterministic rule engine over compiled policy. Same facts → same ruling.
- **Policy is compiled, not searched at decision time.** Policy documents → atomic clauses (IDs + source text) → typed rules (JSON Logic) → versioned **rule pack**. Embeddings are never the decision path for policy.
- **Agents pick procedures; they don't invent them.** At runtime the agent selects an approved procedure (from a library learned at onboarding and approved by the merchant). Neighbour cases fill details and detect drift; they never decide.
- **Every action passes a policy decision point (PDP).** Before any action executes (refund, label, points), call `authorize(actor, action, resource, facts) → allow | deny | needs_approval` with clause IDs. Amounts above the merchant's auto-approve limit → `needs_approval`.
- **Ontology-first.** Procedures and rules reference only Umpyr ontology fields (`Order`, `Shipment`, `Item`, `Booking`, `Flight`, `Bag`, `Customer`, `Case`), never vendor-specific fields. Adapters map merchant data in.
- **Audit everything.** Each ruling stores ticket ID, procedure@version, rule pack version, facts, checks, decision and a SHA-256 hash of that record.
- **PII masked on ingest** (emails, phones, names, card numbers). Store masked text only.
- **Learning from history:** train/route on staff behaviour, but treat drift (staff actions that contradict policy) as findings, not labels. Gold eval labels = policy-consistent outcomes only.

## 4. Architecture

| Layer | Choice |
|---|---|
| Front end | Vite + React + TypeScript, deployed to **Cloudflare Pages** on `umpyr.org` (domain and DNS already on Cloudflare). Routes: `/try`, `/<client>`, `/<client>/onboard`, `/<client>/setup` |
| Auth | **Cloudflare Access** (email one-time code) on `/<client>/*`. No custom auth code |
| Uploads | **Cloudflare R2** with presigned URLs issued by the API |
| Engine | **Python 3.12 + FastAPI** on **Railway**: one web service + one background worker (Postgres-backed job queue is fine; no Redis needed for POC) |
| Database | **Railway Postgres + pgvector** |
| LLM | **Anthropic API** (Claude, tool use) routed via **Cloudflare AI Gateway** for logging and cost caps. Model name configurable via env |
| Embeddings | Voyage AI (configurable; abstract behind an interface) |
| Progress streaming | Server-sent events from FastAPI |
| Rule engine | JSON Logic evaluator in Python + our own thin wrapper for clause refs and precedence |

Secrets only via env vars (`.env.example` committed, `.env` git-ignored). Never commit keys.

### Repo layout
```
apps/web/              React front end (Cloudflare Pages)
services/engine/       FastAPI app + worker
  ingest/              file parsing, column-mapping agent, PII masking
  learn/               embeddings, clustering, taxonomy, router, procedure mining
  compile/             policy → clauses → rules → rule pack
  resolve/             chat agent, tools, PDP, audit
  eval/                replay harness
packages/ontology/     Pydantic models + exported JSON Schema
packages/rules/        rule format, evaluator, precedence, tests
samples/rockler/       synthetic tickets, orders, public policy sources
samples/alaska/        same
infra/                 wrangler.toml, railway.json, Access notes
docs/                  ARCHITECTURE.md, decisions log
```

## 5. Onboarding pipeline (worker jobs, each step streamed to the UI)

1. **Ingest**
   - Tickets: accept CSV, XLSX, JSON/JSONL (Zendesk/Gorgias-style exports) and `.eml`/`.mbox`. A **column-mapping agent** (Claude) maps arbitrary columns to the canonical ticket schema and shows the mapping for approval. Also publish `samples/ticket_template.csv` with: `ticket_id, created_at, channel, subject, thread, agent_actions, resolution, amount, order_id`.
   - Policies: PDF (pymupdf), DOCX (python-docx), Markdown, or a URL (trafilatura crawl, same domain, depth ≤ 2).
   - Orders (optional): CSV keyed by `order_id` (or booking/PNR) — stand-in for live system lookups.
   - Mask PII before storage.
2. **Mine:** Claude extracts each thread into a structured case record (category TBD, facts, ordered staff actions, outcome, amount, hours to resolve).
3. **Taxonomy:** embed cases → cluster (HDBSCAN, fallback k-means) → Claude names clusters → merchant confirms/merges. Pain score = volume × avg hours to resolve.
4. **Router:** train a small classifier (logistic regression on embeddings is fine for POC) with calibrated confidence. Below 0.70 → LLM fallback; LLM can also detect multi-issue tickets and split them.
5. **Procedure mining:** per category, process-mine staff action sequences → common path (steps in ≥ 60% of cases) → Claude drafts a procedure (steps, required facts, outcome branches) → bind clauses to steps → merchant approves.
6. **Policy compile:** Claude segments documents into clauses (`RK-RET-1`…), converts each to a typed rule over ontology fields with an effect, attaches source text and URL. Precedence: law/regulation > contract of carriage/terms > customer commitment > goodwill. Flag conflicts.
7. **Policy vs practice:** replay rule pack over historical cases. Mismatches → **drift findings**; consistent patterns with no clause → **unwritten-rule candidates**. Merchant can mark "policy is right / update policy / adopt as rule / ignore".
8. **Eval set:** gold = policy-consistent cases; drift held for review; train/test split. Report agreement %.

## 6. Resolve runtime (`/<client>` chat)

Claude agent loop with these tools: `classify`, `find_neighbours`, `get_procedure`, `lookup_order`, `extract_facts`, `authorize` (PDP), `rule`, `draft_reply`. Multi-turn: hold state, ask for missing facts (e.g. photos), concede/hold positions within rules, escalate on termination conditions.

UI:
- **Customer chat** on the left.
- **Ops view** on the right: category + confidence, 3 neighbour cases with drift flags, selected procedure@version, a per-step table (policy clause | ontology fields with values | check result), ruling (decision, remedy, amount, fault, authority), governance strip (PII masked, policy version pinned, authority, supervisor check, audit hash).
- Toggles: mode **Autonomous / Assist / Shadow**; reply channel **Email / Customer's AI agent** (agent channel renders a JSON message, protocol `umpyr-resolve/0.1`); auto-approve limit.
- **Merchant chat** tab: questions about their own data ("what drifts most?", "top categories by pain").
- **Supervisor check:** a second model call verifies the reply matches the ruling and promises nothing beyond policy.

Lifecycle note (stub for now, design for later): after customer resolution, a close-out step — for Rockler, disposition of the damaged item (restock / open-box / return to vendor / carrier claim); for Alaska, recovery within policy. Include it as a placeholder step in procedures.

## 7. Sample data (phase 1 ships with these; no uploads needed to demo)

Fetch and store the **public policy sources** (save raw HTML/PDF + extracted text, with URL and fetch date):
- Rockler: https://www.rockler.com/faq-ordering and https://www.rockler.com/return-policy
- Alaska: https://www.alaskaair.com/content/legal/contract-of-carriage (renders via JS — use a headless browser or note it as a gap), customer service commitment, baggage issues page, optional services & fees, credit certificate terms; US DOT refund rule (automatic refunds for cancellations/significant changes when the passenger declines the alternative).

Generate **synthetic tickets** (realistic threads with staff replies and actions, fake PII so masking is demonstrable, deliberately ~15% drift cases):
- Rockler (~300 tickets): damaged/defective on arrival incl. freight, change-of-mind returns (incl. power tools, SawStop/Laguna non-returnable, clearance final sale, restocking fee, rewards-card deduction), lifetime guarantee (router bits, drawer slides, no receipt), backorders, order changes/cancellations.
- Alaska (~300 tickets): cancellations/refunds vs credit, delays and controllable vs uncontrollable, bag service guarantee (20 minutes), delayed/damaged bags (report on file), seat/ancillary refunds, loyalty (Atmos) questions.
- Matching synthetic orders/bookings files.
- Then generate **~1,000 eval scenarios per client** (ticket + facts + expected ruling) from the compiled rules for the replay harness.

Mark all synthetic data clearly as synthetic in the UI.

## 8. Milestones

- **M1 — Skeleton:** repo, ontology package, rule engine with unit tests, Postgres schema, FastAPI health, React shell, `.env.example`. Local `docker compose` (Postgres+pgvector) for dev.
- **M2 — Sandboxes:** policy fetch + compile for Rockler and Alaska, synthetic data, onboarding pipeline run offline, results in DB.
- **M3 — Resolve chat:** `/rockler` and `/alaska` with customer chat + ops view + toggles + audit. **This is the first demo-able milestone.**
- **M4 — Upload flow:** `/try` → new workspace → upload → live onboarding with column-mapping approval → setup/approve → chat.
- **M5 — Eval scoreboard + deploy:** replay harness, agreement metrics, Railway + Cloudflare Pages deploy, Cloudflare Access on client paths, AI Gateway. Ask me before any deploy or DNS change.

Acceptance for each milestone: tests pass, a short README section explains how to run it, and you show me a demo path.

## 9. Constraints and working style

- Keep it simple; this is a POC. Prefer boring, well-known libraries. No Kubernetes, no microservices beyond web + worker.
- Write `docs/ARCHITECTURE.md` and keep a short `docs/DECISIONS.md` log.
- Tests: rule engine and PDP must have thorough unit tests (deterministic behaviour is the product).
- Uploaded client data: auto-delete after 14 days (configurable); masked on ingest; per-tenant isolation in every query.
- Don't make up policy. Every rule must cite a clause with source text and URL; if a policy is ambiguous, flag it rather than guess.
- Design: clean ops-console look. Brand colours: teal and amber (U-shaped logo, two arms meeting with a dot). Light and dark mode.
- Ask me before: deploying, touching DNS/Cloudflare settings, spending on paid APIs beyond dev usage, or anything irreversible.
