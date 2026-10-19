"""Local, dependency-free embedding fallback (no API key needed).

HashingVectorizer (word + bigram, 1024 features) with L2 normalisation. Deterministic, no training,
decent for clustering lexically similar tickets. Used when VOYAGE_API_KEY is absent so the pipeline
runs end to end; switch to Voyage for real semantic quality.
"""

from __future__ import annotations

import logging

from .base import EmbeddingResult

log = logging.getLogger(__name__)


class LocalHashEmbedder:
    name = "local-hash"

    def __init__(self, dim: int = 1024):
        from sklearn.feature_extraction.text import HashingVectorizer

        self.dim = dim
        self.vec = HashingVectorizer(
            n_features=dim, ngram_range=(1, 2), norm="l2", alternate_sign=True, stop_words="english"
        )

    def embed(
        self, texts: list[str], *, step: str, workspace_id: str | None, input_type: str = "document"
    ) -> EmbeddingResult:
        m = self.vec.transform(texts)
        vectors = [row.toarray()[0].tolist() for row in m]
        return EmbeddingResult(vectors=vectors, model="local-hash-1024", tokens=0, cost_usd=0.0)
