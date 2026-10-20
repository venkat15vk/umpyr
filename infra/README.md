# infra

Deployment for the POC: **Railway** runs the engine (Docker), **Cloudflare Pages** serves the web app and
holds the shared-password gate plus the `/api/*` proxy. Step-by-step runbook: [DEPLOY.md](DEPLOY.md).

- `Dockerfile` — engine image (build context = repo root); `start.sh` migrates, seeds from `samples/`, serves
- `../railway.json` — Railway build/deploy config (Dockerfile path, health check)
- `../apps/web/functions/_middleware.ts` — password cookie, `/login`, `/logout`, `/api/*` proxy with gateway header
- `../apps/web/wrangler.toml` — Pages project config (`npx wrangler@3 pages deploy` from `apps/web`)

Not deployed for the POC: the worker (only `/try` needs it), AI Gateway, R2, Cloudflare Access.
