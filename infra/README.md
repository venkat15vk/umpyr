# infra

Deployment config lands in M5 (Railway for the engine, Cloudflare Pages for the web app,
Cloudflare Access on `/<client>/*`, Cloudflare AI Gateway in front of the Anthropic API).
Nothing here is deployed without an explicit go-ahead.

- `wrangler.toml` — Cloudflare Pages project (M5)
- `railway.json` — Railway web + worker services (M5)
- `ACCESS.md` — Cloudflare Access notes (M5)
