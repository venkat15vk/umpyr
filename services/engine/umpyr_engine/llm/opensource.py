"""Stub for an open-source backend (LLM_PROVIDER=opensource).

Intended shape: an OpenAI-compatible local server (vLLM / Ollama) with the same
LLMRequest/LLMResponse contract. Not implemented in the POC.
"""

from __future__ import annotations

from .base import LLMRequest, LLMResponse


class OpenSourceProvider:
    name = "opensource"

    def __init__(self, *args, **kwargs):
        pass

    def complete(self, req: LLMRequest) -> LLMResponse:
        raise NotImplementedError("LLM_PROVIDER=opensource is a stub in this POC; set LLM_PROVIDER=claude")

    def batch(self, reqs: list[LLMRequest], poll_seconds: float = 15.0) -> list[LLMResponse | None]:
        raise NotImplementedError("LLM_PROVIDER=opensource is a stub in this POC; set LLM_PROVIDER=claude")
