#!/usr/bin/env bash
# One-command releases driven by tokens in the repo-root .env (never printed, never committed).
#
#   infra/deploy.sh bootstrap  FIRST TIME: create Railway project + Postgres + service, Pages project, secrets,
#                              custom domain + DNS record, deploy everything, verify. Idempotent: safe to re-run.
#   infra/deploy.sh engine     build + deploy the engine to Railway        (needs RAILWAY_API_TOKEN or RAILWAY_TOKEN)
#   infra/deploy.sh web        build + deploy the web app to Cloudflare    (needs CLOUDFLARE_API_TOKEN)
#   infra/deploy.sh secrets    push DEMO_PASSWORD / SESSION_SECRET / API_GATEWAY_SECRET / API_ORIGIN to Pages
#                              and API_GATEWAY_SECRET / ANTHROPIC_API_KEY / CLAUDE_MODEL / DAILY_COST_CAP_USD to Railway
#   infra/deploy.sh all        secrets, engine, web
#
# Values come from .env (see .env.example, "Deployment" block). .env is loaded via python-dotenv into this
# process only and secrets are piped through stdin, so nothing is echoed to the terminal or shell history.
# Generated secrets and discovered ids (Railway domain, Cloudflare account id) are kept in infra/.deploy.state
# (gitignored, mode 600) so later runs reuse them.
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

STATE="$ROOT/infra/.deploy.state"
if [ -f "$STATE" ]; then  # values from a previous bootstrap; .env always wins
  while IFS='=' read -r k v; do [ -n "$k" ] && [ -z "${!k:-}" ] && export "$k=$v"; done < "$STATE"
fi

RAILWAY_PROJECT="${RAILWAY_PROJECT:-umpyr}"
RAILWAY_SERVICE="${RAILWAY_SERVICE:-umpyr-engine}"
PAGES_PROJECT="${PAGES_PROJECT:-umpyr-web}"
PUBLIC_HOST="${PUBLIC_HOST:-www.umpyr.org}"
ZONE_NAME="${ZONE_NAME:-umpyr.org}"
CF_API="https://api.cloudflare.com/client/v4"
WRANGLER="npx -y wrangler@3"

need() { for v in "$@"; do [ -n "${!v:-}" ] || { echo "missing $v in .env" >&2; exit 1; }; done; }
need_railway() { [ -n "${RAILWAY_API_TOKEN:-}${RAILWAY_TOKEN:-}" ] || { echo "missing RAILWAY_API_TOKEN (account token) or RAILWAY_TOKEN (project token) in .env" >&2; exit 1; }; }
state_get() { [ -f "$STATE" ] && sed -n "s/^$1=//p" "$STATE" | tail -1 || true; }
state_set() { touch "$STATE"; chmod 600 "$STATE"; { grep -v "^$1=" "$STATE" || true; echo "$1=$2"; } > "$STATE.tmp"; mv "$STATE.tmp" "$STATE"; }
ensure_secret() { # NAME: .env value, else state, else generate
  local name="$1" val="${!1:-}"
  [ -n "$val" ] || val="$(state_get "$name")"
  [ -n "$val" ] || { val="$(openssl rand -hex 32)"; echo "   generated $name (kept in infra/.deploy.state)"; }
  state_set "$name" "$val"; export "$name=$val"
}
cf() { curl -sS -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" -H "Content-Type: application/json" "$@"; }
jpy() { uv run --no-sync python -c "import json,sys; d=json.load(sys.stdin); $1"; }

# ------------------------------------------------------------------ release pieces
deploy_engine() {
  need_railway
  echo "==> Railway: $RAILWAY_SERVICE"
  railway up --service "$RAILWAY_SERVICE" --detach
}

deploy_web() {
  need CLOUDFLARE_API_TOKEN
  echo "==> build web (public demo)"
  ( cd apps/web && npm ci --no-audit --no-fund --silent && VITE_API_BASE_URL=/api VITE_PUBLIC_DEMO=true npm run build --silent )
  echo "==> Cloudflare Pages: $PAGES_PROJECT"
  ( cd apps/web && $WRANGLER pages deploy --project-name "$PAGES_PROJECT" --branch main --commit-dirty=true )
}

push_pages_secret() { # NAME (value read from the env var of the same name)
  local name="$1"; [ -n "${!name:-}" ] || { echo "   skip $name (unset)"; return; }
  printf '%s' "${!name}" | ( cd apps/web && $WRANGLER pages secret put "$name" --project-name "$PAGES_PROJECT" >/dev/null )
  echo "   pages   $name"
}
push_railway_var() { # NAME (value read from the env var of the same name)
  local name="$1"; [ -n "${!name:-}" ] || { echo "   skip $name (unset)"; return; }
  printf '%s' "${!name}" | railway variables set --service "$RAILWAY_SERVICE" --skip-deploys --stdin "$name" >/dev/null
  echo "   railway $name"
}

deploy_secrets_railway() {
  for n in ANTHROPIC_API_KEY CLAUDE_MODEL API_GATEWAY_SECRET DAILY_COST_CAP_USD VOYAGE_API_KEY; do push_railway_var "$n"; done
  railway variables set --service "$RAILWAY_SERVICE" --skip-deploys \
    LLM_PROVIDER=claude LLM_BATCH=false ENGINE_ENV=prod SEED_ON_BOOT=true ASSUME_REFERENCE_EXISTS=true \
    EMBEDDING_PROVIDER="${EMBEDDING_PROVIDER_PROD:-local}" CORS_ORIGINS="https://$PUBLIC_HOST" >/dev/null
  echo "   railway fixed settings"
}

deploy_secrets() {
  need_railway; need CLOUDFLARE_API_TOKEN
  ensure_secret SESSION_SECRET; ensure_secret API_GATEWAY_SECRET
  echo "==> secrets -> Cloudflare Pages"
  for n in DEMO_PASSWORD SESSION_SECRET API_GATEWAY_SECRET API_ORIGIN; do push_pages_secret "$n"; done
  echo "==> variables -> Railway ($RAILWAY_SERVICE)"
  deploy_secrets_railway
}

# ------------------------------------------------------------------ first-time bootstrap
bootstrap_railway() {
  need_railway
  echo "==> Railway project '$RAILWAY_PROJECT'"
  if ! railway status >/dev/null 2>&1; then
    railway init --name "$RAILWAY_PROJECT" >/dev/null
    echo "   created project"
  fi
  local svc
  svc="$(railway status --json 2>/dev/null | jpy 'print(" ".join(n.get("name","") for e in (d.get("services") or {}).get("edges",[]) for n in [e.get("node",{})]))' 2>/dev/null || true)"
  if ! grep -qi postgres <<<"$svc"; then echo "   adding Postgres"; railway add --database postgres >/dev/null; sleep 5; fi
  if ! grep -qw "$RAILWAY_SERVICE" <<<"$svc"; then echo "   adding service $RAILWAY_SERVICE"; railway add --service "$RAILWAY_SERVICE" >/dev/null; fi
  railway service "$RAILWAY_SERVICE" >/dev/null 2>&1 || true
  echo "   variables"
  railway variables set --service "$RAILWAY_SERVICE" --skip-deploys 'DATABASE_URL=${{Postgres.DATABASE_URL}}' >/dev/null
  deploy_secrets_railway
  echo "   deploying (first build takes 3-5 minutes)"
  railway up --service "$RAILWAY_SERVICE" --detach >/dev/null
  local dom; dom="$(state_get API_ORIGIN)"
  if [ -z "$dom" ]; then
    dom="$(railway domain --service "$RAILWAY_SERVICE" --json 2>/dev/null | grep -o '[a-z0-9.-]*\.up\.railway\.app' | head -1 || true)"
    [ -n "$dom" ] || dom="$(railway domain --service "$RAILWAY_SERVICE" 2>&1 | grep -o '[a-z0-9.-]*\.up\.railway\.app' | head -1 || true)"
    [ -n "$dom" ] || { echo "could not determine the Railway domain; run: railway domain --service $RAILWAY_SERVICE" >&2; exit 1; }
    dom="https://$dom"; state_set API_ORIGIN "$dom"
  fi
  export API_ORIGIN="$dom"; echo "   API_ORIGIN=$API_ORIGIN"
  echo "   waiting for /health"
  for _ in $(seq 1 60); do curl -fsS "$API_ORIGIN/health" >/dev/null 2>&1 && break; sleep 10; done
  curl -fsS "$API_ORIGIN/health" || { echo "engine not healthy yet; check: railway logs --service $RAILWAY_SERVICE" >&2; exit 1; }; echo
}

bootstrap_cloudflare() {
  need CLOUDFLARE_API_TOKEN DEMO_PASSWORD API_ORIGIN
  if [ -z "${CLOUDFLARE_ACCOUNT_ID:-}" ]; then
    CLOUDFLARE_ACCOUNT_ID="$(cf "$CF_API/accounts?per_page=1" | jpy 'print(d["result"][0]["id"])')"
    export CLOUDFLARE_ACCOUNT_ID; state_set CLOUDFLARE_ACCOUNT_ID "$CLOUDFLARE_ACCOUNT_ID"
  fi
  echo "==> Cloudflare Pages project '$PAGES_PROJECT' (account $CLOUDFLARE_ACCOUNT_ID)"
  if ! cf "$CF_API/accounts/$CLOUDFLARE_ACCOUNT_ID/pages/projects/$PAGES_PROJECT" | jpy 'sys.exit(0 if d.get("success") else 1)'; then
    ( cd apps/web && $WRANGLER pages project create "$PAGES_PROJECT" --production-branch main >/dev/null )
    echo "   created"
  fi
  echo "   secrets"
  for n in DEMO_PASSWORD SESSION_SECRET API_GATEWAY_SECRET API_ORIGIN; do push_pages_secret "$n"; done
  deploy_web
  echo "==> custom domain $PUBLIC_HOST"
  local zone; zone="$(cf "$CF_API/zones?name=$ZONE_NAME" | jpy 'print(d["result"][0]["id"] if d["result"] else "")')"
  [ -n "$zone" ] || { echo "zone $ZONE_NAME not visible to this token (needs Zone:Read + DNS:Edit on it)" >&2; exit 1; }
  cf -X POST "$CF_API/accounts/$CLOUDFLARE_ACCOUNT_ID/pages/projects/$PAGES_PROJECT/domains" --data "{\"name\":\"$PUBLIC_HOST\"}" \
    | jpy 'ok=d.get("success") or any(e.get("code")==8000014 for e in d.get("errors",[])); print("   pages domain", "ok" if ok else d.get("errors")); sys.exit(0 if ok else 1)'
  local target="$PAGES_PROJECT.pages.dev"
  local rec; rec="$(cf "$CF_API/zones/$zone/dns_records?name=$PUBLIC_HOST" | jpy 'r=d["result"]; print(r[0]["id"] if r else "")')"
  local body="{\"type\":\"CNAME\",\"name\":\"$PUBLIC_HOST\",\"content\":\"$target\",\"proxied\":true,\"ttl\":1}"
  if [ -n "$rec" ]; then
    cf -X PUT "$CF_API/zones/$zone/dns_records/$rec" --data "$body" | jpy 'print("   dns record updated" if d.get("success") else d.get("errors"))'
  else
    cf -X POST "$CF_API/zones/$zone/dns_records" --data "$body" | jpy 'print("   dns record created" if d.get("success") else d.get("errors"))'
  fi
  echo "   waiting for https://$PUBLIC_HOST/login (certificate can take a few minutes)"
  for _ in $(seq 1 60); do [ "$(curl -s -o /dev/null -w '%{http_code}' "https://$PUBLIC_HOST/login")" = "200" ] && break; sleep 10; done
  echo "   https://$PUBLIC_HOST/login -> $(curl -s -o /dev/null -w '%{http_code}' "https://$PUBLIC_HOST/login")"
}

bootstrap() {
  need DEMO_PASSWORD
  ensure_secret SESSION_SECRET; ensure_secret API_GATEWAY_SECRET
  bootstrap_railway
  bootstrap_cloudflare
  echo
  echo "Done. Share: https://$PUBLIC_HOST/alaska  https://$PUBLIC_HOST/rockler  (password: DEMO_PASSWORD from .env)"
  echo "Later releases: make deploy"
}

case "${1:-}" in
  bootstrap) bootstrap ;;
  engine)    deploy_engine ;;
  web)       deploy_web ;;
  secrets)   deploy_secrets ;;
  all)       deploy_secrets; deploy_engine; deploy_web ;;
  *) sed -n '2,14p' "$0"; exit 1 ;;
esac
