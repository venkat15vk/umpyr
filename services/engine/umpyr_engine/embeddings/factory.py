from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from ..db.base import get_session_factory
from .base import EmbeddingProvider


@lru_cache
def get_embedder() -> EmbeddingProvider:
    from .voyage import VoyageEmbedder

    return VoyageEmbedder(get_settings(), get_session_factory())
