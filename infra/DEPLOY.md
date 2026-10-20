# Deploying the POC to www.umpyr.org

Target: `https://www.umpyr.org/alaska` and `https://www.umpyr.org/rockler`, both behind one shared password.

```
browser ──► Cloudflare Pages (www.umpyr.org)
             │  functions/_middleware.ts: password cookie, /login, /api/* proxy
             └──► Railway: umpyr-engine (FastAPI, Dockerfile)  ──► Railway Postgres (pgvector)
                                                               ──► Anthropic API (Haiku, daily cap)
```

Nobody reaches the engine directly: the Railway service refuses any request that does not carry the
`x-umpyr-gateway` header, and only the Pages middleware adds it, after the password check.

Everything below is done from your own logged-in dashboards or terminal. Nothing here needs a GitHub
repo; both platforms accept a direct upload from your machine.

---

## 0. One-time prep (your laptop, 2 minutes)

```bash
cd ~/Desktop/Startup/umpyr
openssl rand -hex 32    # -> value for API_GATEWAY_SECRET  (copy it, you need it twice)
openssl rand -hex 32    # -> value for SESSION_SECRET
```

Keep those two values somewhere private for the steps below. The demo password is the one you chose;
it is never written into the repo.

---

## 1. Railway: engine + database (about 15 minutes)

### 1a. Create the project and database

1. https://railway.com → **New Project** → **Deploy PostgreSQL**.
   Railway's standard Postgres image ships with the `pgvector` extension; the migration runs
   `CREATE EXTENSION IF NOT EXISTS vector` itself. If the first deploy log ever shows
   `extension "vector" is not available`, delete that database and add the **pgvector** template
   from the Railway template gallery instead.
2. Rename the project `umpyr` (project settings, top-left).

### 1b. Deploy the engine from your machine

```bash
cd ~/Desktop/Startup/umpyr
railway login                     # opens a browser
railway link                      # pick the project "umpyr" you created in 1a
railway add --service umpyr-engine
railway service umpyr-engine      # make it the linked service for the commands below
```

Set the variables **before** the first deploy so the boot script can migrate and seed. Either in the
dashboard (service → **Variables** → *Raw editor*) or with the CLI (one command, `--skip-deploys`
avoids a redeploy per variable):

```bash
railway variables set --skip-deploys \
  'DATABASE_URL=${{Postgres.DATABASE_URL}}' \
  CLAUDE_MODEL=<same value as in your .env> \
  LLM_PROVIDER=claude \
  LLM_BATCH=false \
  DAILY_COST_CAP_USD=1 \
  ASSUME_REFERENCE_EXISTS=true \
  EMBEDDING_PROVIDER=local \
  ENGINE_ENV=prod \
  SEED_ON_BOOT=true \
  CORS_ORIGINS=https://www.umpyr.org \
  API_GATEWAY_SECRET=<value 1 from step 0>

# the API key without it landing in shell history:
railway variables set --stdin ANTHROPIC_API_KEY     # paste the key, Enter, Ctrl-D
```

Notes
- `${{Postgres.DATABASE_URL}}` is a Railway reference to the database you created; if you named the
  database service differently, use that name. It must be the **internal** URL (`postgres.railway.internal`).
- `EMBEDDING_PROVIDER=local` keeps the hash embedder that the sandboxes were built with. Only set
  `voyage` plus `VOYAGE_API_KEY` if you also rebuild the sandboxes with Voyage.
- `DAILY_COST_CAP_USD=1` means strangers can burn at most $1 of Haiku per UTC day; the UI shows
  "Demo budget reached for today" after that. Raise it if the demo gets busy.

Then deploy:

```bash
railway up --detach
```

The Dockerfile at `infra/Dockerfile` builds the engine with `uv`. On boot `infra/start.sh` runs
`umpyr db upgrade`, then `umpyr sandbox seed rockler alaska` (loads both merchants from the cached
`samples/` folder: tickets, records, rule packs, cases, taxonomy, procedures, drift, eval; **zero LLM
calls**, the daily cap is forced to 0 inside that process so any uncached step would fail loudly instead
of spending), then starts the API. First boot takes 2 to 4 minutes; later boots skip the seed because
the tickets are already there.

### 1c. Expose it and verify

1. Service → **Settings** → **Networking** → **Generate Domain**. You get something like
   `umpyr-engine-production.up.railway.app`. Copy it; it becomes `API_ORIGIN` below.
2. Check:
   ```bash
   curl https://<railway-domain>/health          # {"status":"ok","db":true,...}
   curl -i https://<railway-domain>/workspaces   # 401 gateway_required  (correct: not reachable without the gateway)
   railway logs | tail -30                       # should show "ingest ... 300 tickets" for both merchants on first boot
   ```

---

## 2. Cloudflare Pages: web app + password (about 10 minutes)

### 2a. Build and upload from your machine

```bash
cd ~/Desktop/Startup/umpyr/apps/web
npm ci
VITE_API_BASE_URL=/api VITE_PUBLIC_DEMO=true npm run build
npx wrangler@3 login                               # opens a browser
npx wrangler@3 pages project create umpyr-web --production-branch main
npx wrangler@3 pages deploy                        # reads wrangler.toml: uploads dist/ + functions/
```

`VITE_PUBLIC_DEMO=true` compiles out `/try`, onboarding and setup, so the public build only has the
two sandboxes. `VITE_API_BASE_URL=/api` makes the browser call the same origin; the middleware proxies
`/api/*` to Railway.

### 2b. Secrets and variables

Dashboard → **Workers & Pages** → `umpyr-web` → **Settings** → **Variables and Secrets** → *Production*:

| Name | Type | Value |
|---|---|---|
| `DEMO_PASSWORD` | Secret | the shared password |
| `SESSION_SECRET` | Secret | value 2 from step 0 |
| `API_GATEWAY_SECRET` | Secret | value 1 from step 0 (identical to Railway) |
| `API_ORIGIN` | Text | `https://<railway-domain>` (no trailing slash) |

Or from the terminal: `npx wrangler@3 pages secret put DEMO_PASSWORD` (prompts, nothing echoed) and so on.

Redeploy once after setting them so the functions pick them up: `npx wrangler@3 pages deploy`.

### 2c. Custom domain

Dashboard → `umpyr-web` → **Custom domains** → **Set up a custom domain** → `www.umpyr.org`.
Because the `umpyr.org` zone is in the same Cloudflare account, Cloudflare adds the CNAME itself and
issues the certificate (usually under 5 minutes). Add `umpyr.org` the same way if you want the bare
domain to work too.

### 2d. Verify

```
https://www.umpyr.org/alaska        -> redirects to /login
enter the password                  -> lands on /alaska, chat works
https://www.umpyr.org/api/health    -> JSON once logged in, 401 before
https://<railway-domain>/workspaces -> 401 always
```

Cookie lasts 7 days; `/logout` clears it. Pages are sent with `noindex` and `robots.txt` disallows crawling.

---

## 3. Sending it out

Give people:

- `https://www.umpyr.org/alaska` and `https://www.umpyr.org/rockler`
- the password
- a sentence that any confirmation code or order number they type is accepted (POC behaviour) and that
  all customer data is synthetic.

Watch spend: Railway → `umpyr-engine` → **Logs**, or
`curl -H "x-umpyr-gateway: <secret>" https://<railway-domain>/admin/spend`.

---

## 4. Updating later

```bash
# engine
cd ~/Desktop/Startup/umpyr && railway up --detach
# web
cd apps/web && VITE_API_BASE_URL=/api VITE_PUBLIC_DEMO=true npm run build && npx wrangler@3 pages deploy
```

To change the password: update the `DEMO_PASSWORD` secret and redeploy Pages. Existing cookies stay
valid until they expire (7 days); rotate `SESSION_SECRET` too to log everyone out immediately.

---

## 5. Not deployed on purpose

- **Worker** (`umpyr worker`): only needed for `/try` uploads and retention; the public build hides `/try`.
  To add it later: a second Railway service from the same Dockerfile with start command
  `cd /app/services/engine && umpyr worker`, same variables.
- **Cloudflare AI Gateway**, **R2 uploads**, **Cloudflare Access**: the shared-password middleware
  replaces Access for this POC.

## Costs

- Railway Hobby plan: $5/month includes usage credit; this engine idles at well under that. Postgres a few hundred MB.
- Cloudflare Pages: free tier (functions included).
- Anthropic: capped by `DAILY_COST_CAP_USD`.
