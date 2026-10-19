"""Provider-agnostic LLM interface. All runtime calls go through this."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMRequest:
    """One model call.

    system: stable instructions. Keep it byte-stable across calls so prompt caching works.
    messages: Anthropic-style messages (role/content).
    tools: Anthropic tool definitions (optional).
    output_schema: JSON Schema for structured output (optional). When set, `LLMResponse.parsed` holds the JSON.
    step: pipeline step name for spend accounting (e.g. "mine", "classify", "draft_reply").
    """

    step: str
    messages: list[dict[str, Any]]
    system: str | None = None
    tools: list[dict[str, Any]] | None = None
    output_schema: dict[str, Any] | None = None
    max_tokens: int = 2048
    effort: str | None = None
    workspace_id: str | None = None
    cache_system: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str
    content: list[dict[str, Any]]
    tool_uses: list[ToolUse]
    stop_reason: str | None
    usage: Usage
    model: str
    parsed: Any = None
    request_id: str | None = None
    refusal_category: str | None = None
    is_batch: bool = False
    cost_usd: float = 0.0

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


class LLMProvider(Protocol):
    name: str

    def complete(self, req: LLMRequest) -> LLMResponse: ...

    def batch(self, reqs: list[LLMRequest], poll_seconds: float = 15.0) -> list[LLMResponse | None]:
        """Run many requests offline at batch pricing. Returns results aligned with `reqs`;
        None where a request failed."""
        ...
