from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    tokens: int
    cost_usd: float


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed(
        self, texts: list[str], *, step: str, workspace_id: str | None, input_type: str = "document"
    ) -> EmbeddingResult: ...
