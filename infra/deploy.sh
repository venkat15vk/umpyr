#!/usr/bin/env bash
# One-command releases driven by tokens in the repo-root .env (never printed, never committed).
#
#   infra/deploy.sh engine     build + deploy the engine to Railway        (needs RAILWAY_TOKEN)
#   infra/deploy.sh web        build + deploy the web app to Cloudflare    (needs CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID)
#   infra/deploy.sh secrets    push DEMO_PASSWORD / SESSION_SECRET / API_GATEWAY_SECRET / API_ORIGIN to Pages
#                              and API_GATEWAY_SECRET / ANTHROPIC_API_KEY / CLAUDE_MODEL / DAILY_COST_CAP_USD to Railway
#   infra/deploy.sh all        secrets, engine, web
#
# Values come from .env (see .env.example, "Deployment" block). .env is loaded via python-dotenv into this
# process only and secrets are piped through stdin, so nothing is echoed to the terminal or shell history.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Load .env with python-dotenv semantics (quotes, comments, parentheses) into this process only; nothing is printed.
if [ -z "${UMPYR_ENV_LOADED:-}" ] && [ -f .env ]; then
  export UMPYR_ENV_LOADED=1
  exec uv run --no-sync python - "$0" "$@" <<'PY'
import os, subprocess, sys
from dotenv import dotenv_values
env = {**os.environ, **{k: v for k, v in dotenv_values(".env").items() if v is not None}}
sys.exit(subprocess.call(sys.argv[1:], env=env))
PY
fi

RAILWAY_SERVICE="${RAILWAY_SERVICE:-umpyr-engine}"
PAGES_PROJECT="${PAGES_PROJECT:-umpyr-web}"
WRANGLER="npx -y wrangler@3"

need() { for v in "$@"; do [ -n "${!v:-}" ] || { echo "missing $v in .env" >&2; exit 1; }; done; }

deploy_engine() {
  need RAILWAY_TOKEN
  echo "==> Railway: $RAILWAY_SERVICE"
  railway up --service "$RAILWAY_SERVICE" --detach
}

deploy_web() {
  need CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID
  echo "==> build web (public demo)"
  ( cd apps/web && npm ci --no-audit --no-fund --silent && VITE_API_BASE_URL=/api VITE_PUBLIC_DEMO=true npm run build --silent )
  echo "==> Cloudflare Pages: $PAGES_PROJECT"
  ( cd apps/web && $WRANGLER pages deploy --project-name "$PAGES_PROJECT" --branch main --commit-dirty=true )
}

push_pages_secret() { # NAME  (value read from the env var of the same name)
  local name="$1"; [ -n "${!name:-}" ] || { echo "   skip $name (unset)"; return; }
  printf '%s' "${!name}" | ( cd apps/web && $WRANGLER pages secret put "$name" --project-name "$PAGES_PROJECT" >/dev/null )
  echo "   pages  $name"
}
push_railway_var() { # NAME  (value read from the env var of the same name)
  local name="$1"; [ -n "${!name:-}" ] || { echo "   skip $name (unset)"; return; }
  printf '%s' "${!name}" | railway variables set --service "$RAILWAY_SERVICE" --skip-deploys --stdin "$name" >/dev/null
  echo "   railway $name"
}

deploy_secrets() {
  need RAILWAY_TOKEN CLOUDFLARE_API_TOKEN CLOUDFLARE_ACCOUNT_ID
  echo "==> secrets -> Cloudflare Pages"
  for n in DEMO_PASSWORD SESSION_SECRET API_GATEWAY_SECRET API_ORIGIN; do push_pages_secret "$n"; done
  echo "==> variables -> Railway ($RAILWAY_SERVICE)"
  for n in ANTHROPIC_API_KEY CLAUDE_MODEL API_GATEWAY_SECRET DAILY_COST_CAP_USD VOYAGE_API_KEY; do push_railway_var "$n"; done
  # fixed production settings
  railway variables set --service "$RAILWAY_SERVICE" --skip-deploys \
    LLM_PROVIDER=claude LLM_BATCH=false ENGINE_ENV=prod SEED_ON_BOOT=true ASSUME_REFERENCE_EXISTS=true \
    EMBEDDING_PROVIDER="${EMBEDDING_PROVIDER_PROD:-local}" CORS_ORIGINS="${PUBLIC_ORIGIN:-https://www.umpyr.org}" >/dev/null
  echo "   railway fixed settings"
  echo "   (DATABASE_URL is a Railway reference: set it once in the dashboard as \${{Postgres.DATABASE_URL}})"
}

case "${1:-}" in
  engine)  deploy_engine ;;
  web)     deploy_web ;;
  secrets) deploy_secrets ;;
  all)     deploy_secrets; deploy_engine; deploy_web ;;
  *) sed -n '2,12p' "$0"; exit 1 ;;
esac
