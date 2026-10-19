from __future__ import annotations

import logging
from functools import lru_cache

from ..config import get_settings
from ..db.base import get_session_factory
from .base import EmbeddingProvider

log = logging.getLogger(__name__)


@lru_cache
def get_embedder() -> EmbeddingProvider:
    s = get_settings()
    provider = s.embedding_provider
    if provider == "auto":
        provider = "voyage" if s.voyage_api_key else "local"
    if provider == "voyage":
        from .voyage import VoyageEmbedder

        return VoyageEmbedder(s, get_session_factory())
    if provider == "local":
        log.warning("using LOCAL hash embeddings (VOYAGE_API_KEY not set). Set it for semantic quality.")
        from .local import LocalHashEmbedder

        return LocalHashEmbedder(s.embedding_dim)
    raise RuntimeError(f"unknown EMBEDDING_PROVIDER '{provider}' (auto|voyage|local)")
