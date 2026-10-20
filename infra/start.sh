#!/usr/bin/env bash
# Railway start: migrate, seed the two sandboxes from cached samples (idempotent, zero LLM spend), serve.
set -euo pipefail
cd /app/services/engine
umpyr db upgrade
if [ "${SEED_ON_BOOT:-true}" = "true" ]; then
  umpyr sandbox seed rockler alaska
fi
exec umpyr api --host 0.0.0.0 --port "${PORT:-8000}"
