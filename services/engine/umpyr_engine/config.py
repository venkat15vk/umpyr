"""Settings. Loaded from the repo-root .env at runtime (never printed, never committed).

Secrets are SecretStr so they never appear in logs or reprs.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "packages").is_dir():
            return parent
    return here.parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(repo_root() / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # LLM
    anthropic_api_key: SecretStr | None = None
    anthropic_base_url: str | None = Field(None, description="Set to the Cloudflare AI Gateway URL in prod")
    llm_provider: str = Field("claude", description="claude | opensource")
    claude_model: str = Field("claude-haiku-5-5", description="A Haiku model id; every runtime call uses it")
    daily_cost_cap_usd: Decimal = Field(Decimal("1.00"), description="Block LLM calls once today's spend reaches this")
    llm_default_effort: str = Field("low", description="output_config.effort for runtime calls")
    llm_batch: bool = Field(
        True, description="Use the Batch API (50% price, minutes of queue latency) for offline jobs"
    )
    llm_sync_workers: int = Field(6, description="Concurrent requests when LLM_BATCH=false")

    # Embeddings
    voyage_api_key: SecretStr | None = None
    embedding_provider: str = Field("auto", description="auto | voyage | local (auto = voyage if key set, else local)")
    voyage_model: str = "voyage-3.5"
    embedding_dim: int = 1024

    # Database
    database_url: str = Field(
        "pgserver://.pgdata",
        description="postgresql+psycopg://user:pass@host/db (Docker/Railway) or pgserver://<dir> (embedded dev DB)",
    )
    local_pg_dir: str = Field(".pgdata", description="Data dir for the embedded dev Postgres (umpyr db local)")

    # Engine
    engine_env: str = "dev"
    data_retention_days: int = 14
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    worker_poll_seconds: float = 1.0
    max_upload_tickets: int = 2000
    max_upload_mb: int = 20
    assume_reference_exists: bool = (
        True  # POC: an unknown reference gets a stand-in record cloned from the nearest case
    )

    @property
    def root(self) -> Path:
        return repo_root()

    @property
    def samples_dir(self) -> Path:
        return self.root / "samples"

    @property
    def data_dir(self) -> Path:
        d = self.root / "services" / "engine" / "data"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
