"""Anthropic Claude provider.

Rules enforced here:
- Model comes from settings (CLAUDE_MODEL). Nothing else can pick a model.
- The budget guard is consulted before every call and records every call's usage.
- System prompts and tool definitions carry cache_control so repeated calls hit the prompt cache.
- Haiku 5.5: adaptive thinking is on by default; we pass output_config.effort (default low).
  Haiku 4.5 rejects `effort`, so it is omitted there.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from ..config import Settings
from .base import LLMRequest, LLMResponse, ToolUse, Usage
from .budget import BudgetGuard

log = logging.getLogger(__name__)

ALLOWED_MODEL_PREFIXES = ("claude-haiku-",)


class ClaudeProvider:
    name = "claude"

    def __init__(self, settings: Settings, budget: BudgetGuard):
        if not settings.claude_model.startswith(ALLOWED_MODEL_PREFIXES):
            raise RuntimeError(f"CLAUDE_MODEL must be a Haiku model for this POC (got '{settings.claude_model}').")
        self.model = settings.claude_model
        self.default_effort = settings.llm_default_effort
        self.budget = budget
        kwargs: dict[str, Any] = {}
        if settings.anthropic_api_key:
            kwargs["api_key"] = settings.anthropic_api_key.get_secret_value()
        if settings.anthropic_base_url:
            kwargs["base_url"] = settings.anthropic_base_url
        self.client = anthropic.Anthropic(max_retries=3, timeout=120.0, **kwargs)

    # ---- request building -------------------------------------------------
    @property
    def supports_effort(self) -> bool:
        return not self.model.startswith("claude-haiku-4-5")

    def _params(self, req: LLMRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": req.max_tokens,
            "messages": req.messages,
        }
        if req.system:
            block: dict[str, Any] = {"type": "text", "text": req.system}
            if req.cache_system:
                block["cache_control"] = {"type": "ephemeral"}
            params["system"] = [block]
        if req.tools:
            tools = [dict(t) for t in req.tools]
            if req.cache_system:
                tools[-1]["cache_control"] = {"type": "ephemeral"}
            params["tools"] = tools
        output_config: dict[str, Any] = {}
        if self.supports_effort:
            output_config["effort"] = req.effort or self.default_effort
        if req.output_schema:
            output_config["format"] = {"type": "json_schema", "schema": req.output_schema}
        if output_config:
            params["output_config"] = output_config
        return params

    @staticmethod
    def _parse(msg: Any, req: LLMRequest, is_batch: bool) -> LLMResponse:
        content: list[dict[str, Any]] = []
        tool_uses: list[ToolUse] = []
        texts: list[str] = []
        for block in msg.content:
            d = block.model_dump() if hasattr(block, "model_dump") else dict(block)
            content.append(d)
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use":
                tool_uses.append(ToolUse(id=block.id, name=block.name, input=dict(block.input)))
        text = "".join(texts)
        parsed = None
        if req.output_schema and text and msg.stop_reason != "refusal":
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                log.warning("structured output was not valid JSON (step=%s)", req.step)
        u = msg.usage
        usage = Usage(
            input_tokens=u.input_tokens or 0,
            output_tokens=u.output_tokens or 0,
            cache_write_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        )
        refusal_category = None
        if msg.stop_reason == "refusal" and getattr(msg, "stop_details", None):
            refusal_category = getattr(msg.stop_details, "category", None)
        return LLMResponse(
            text=text,
            content=content,
            tool_uses=tool_uses,
            stop_reason=msg.stop_reason,
            usage=usage,
            model=msg.model,
            parsed=parsed,
            request_id=getattr(msg, "_request_id", None),
            refusal_category=refusal_category,
            is_batch=is_batch,
        )

    # ---- calls ------------------------------------------------------------
    def complete(self, req: LLMRequest) -> LLMResponse:
        self.budget.check()
        started = time.monotonic()
        msg = self.client.messages.create(**self._params(req))
        latency_ms = int((time.monotonic() - started) * 1000)
        resp = self._parse(msg, req, is_batch=False)
        resp.cost_usd = float(
            self.budget.record(
                model=self.model,
                step=req.step,
                workspace_id=req.workspace_id,
                usage=resp.usage,
                is_batch=False,
                request_id=resp.request_id,
                latency_ms=latency_ms,
            )
        )
        return resp

    def batch(self, reqs: list[LLMRequest], poll_seconds: float = 15.0) -> list[LLMResponse | None]:
        if not reqs:
            return []
        self.budget.check()
        requests = [
            Request(custom_id=f"r{i}", params=MessageCreateParamsNonStreaming(**self._params(r)))
            for i, r in enumerate(reqs)
        ]
        batch = self.client.messages.batches.create(requests=requests)
        log.info("batch %s created with %d requests", batch.id, len(reqs))
        while True:
            batch = self.client.messages.batches.retrieve(batch.id)
            if batch.processing_status == "ended":
                break
            time.sleep(poll_seconds)
        out: list[LLMResponse | None] = [None] * len(reqs)
        for result in self.client.messages.batches.results(batch.id):
            idx = int(result.custom_id[1:])
            req = reqs[idx]
            if result.result.type == "succeeded":
                resp = self._parse(result.result.message, req, is_batch=True)
                resp.cost_usd = float(
                    self.budget.record(
                        model=self.model,
                        step=req.step,
                        workspace_id=req.workspace_id,
                        usage=resp.usage,
                        is_batch=True,
                        request_id=batch.id,
                        latency_ms=None,
                    )
                )
                out[idx] = resp
            else:
                log.warning("batch item %s %s: %s", batch.id, result.custom_id, result.result.type)
        return out
