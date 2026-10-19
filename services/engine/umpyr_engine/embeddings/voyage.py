"""Voyage AI embeddings. Usage is logged to `embedding_calls`; it does not count toward the LLM cap."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import voyageai
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings
from ..db.models import EmbeddingCall
from ..llm.pricing import cost_usd
from .base import EmbeddingResult

log = logging.getLogger(__name__)

BATCH = 128


class VoyageEmbedder:
    name = "voyage"

    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]):
        if not settings.voyage_api_key:
            raise RuntimeError("VOYAGE_API_KEY is not set")
        self.client = voyageai.Client(api_key=settings.voyage_api_key.get_secret_value())
        self.model = settings.voyage_model
        self.dim = settings.embedding_dim
        self.session_factory = session_factory

    def embed(
        self, texts: list[str], *, step: str, workspace_id: str | None, input_type: str = "document"
    ) -> EmbeddingResult:
        vectors: list[list[float]] = []
        tokens = 0
        for i in range(0, len(texts), BATCH):
            chunk = texts[i : i + BATCH]
            res = self.client.embed(chunk, model=self.model, input_type=input_type, output_dimension=self.dim)
            vectors.extend(res.embeddings)
            tokens += res.total_tokens or 0
        cost = cost_usd(self.model, tokens, 0)
        with self.session_factory() as s:
            s.add(
                EmbeddingCall(
                    workspace_id=workspace_id,
                    step=step,
                    model=self.model,
                    texts=len(texts),
                    tokens=tokens,
                    cost_usd=cost,
                    day=datetime.now(UTC).date(),
                )
            )
            s.commit()
        log.info("embedded %d texts (%d tokens, $%.6f)", len(texts), tokens, cost)
        return EmbeddingResult(vectors=vectors, model=self.model, tokens=tokens, cost_usd=float(cost))
