# Umpyr Resolve engine (API). Build context = repo root:  docker build -f infra/Dockerfile .
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
WORKDIR /app

# dependency layer (cached unless lockfile / package manifests change)
COPY pyproject.toml uv.lock ./
COPY packages/ontology/pyproject.toml packages/ontology/pyproject.toml
COPY packages/rules/pyproject.toml packages/rules/pyproject.toml
COPY services/engine/pyproject.toml services/engine/pyproject.toml
RUN mkdir -p packages/ontology/umpyr_ontology packages/rules/umpyr_rules services/engine/umpyr_engine \
 && uv sync --frozen --no-dev --no-install-workspace

# source + cached sandbox outputs (samples/ is what `umpyr sandbox seed` loads; no LLM calls)
COPY packages packages
COPY services/engine services/engine
COPY samples samples
COPY infra/start.sh infra/start.sh
RUN uv sync --frozen --no-dev && chmod +x infra/start.sh

ENV PATH="/app/.venv/bin:$PATH" ENGINE_ENV=prod PORT=8000
WORKDIR /app/services/engine
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s CMD curl -fsS http://localhost:${PORT}/health || exit 1
CMD ["/app/infra/start.sh"]
