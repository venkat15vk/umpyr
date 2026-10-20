# Convenience targets. Python via uv, web via npm.
.PHONY: setup db api worker web test lint spend smoke

setup:            ## install python + node deps
	uv sync --all-groups
	cd apps/web && npm install --no-audit --no-fund

db:               ## apply migrations (uses DATABASE_URL; default embedded pgserver)
	cd services/engine && uv run umpyr db upgrade

api:              ## run the FastAPI engine on :8000
	cd services/engine && uv run umpyr api

worker:           ## run the background worker
	cd services/engine && uv run umpyr worker

web:              ## run the Vite dev server on :5173
	cd apps/web && npm run dev

test:             ## all python tests + web typecheck/tests
	uv run pytest -q
	cd apps/web && npm run typecheck && npm test -- --run

lint:
	uv run ruff check .

spend:            ## today's LLM spend by step
	cd services/engine && uv run umpyr spend

smoke:            ## one tiny Haiku call (costs < $0.001)
	cd services/engine && uv run umpyr llm smoke

# --- release (tokens from .env; run as `make deploy`, or `! make deploy` inside Claude Code) ---
.PHONY: deploy deploy-engine deploy-web deploy-secrets
deploy:           ## secrets + engine (Railway) + web (Cloudflare Pages)
	infra/deploy.sh all
deploy-engine:    ## engine only
	infra/deploy.sh engine
deploy-web:       ## web only
	infra/deploy.sh web
deploy-secrets:   ## push secrets/variables from .env to Pages and Railway
	infra/deploy.sh secrets
