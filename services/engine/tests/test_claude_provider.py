"""Provider tests with the Anthropic client mocked: no network, no spend."""

from types import SimpleNamespace

import pytest
from umpyr_engine.config import get_settings
from umpyr_engine.llm.base import LLMRequest
from umpyr_engine.llm.budget import BudgetExceeded
from umpyr_engine.llm.claude import ClaudeProvider


class FakeMessages:
    def __init__(self, text='{"a": 1}', stop_reason="end_turn"):
        self.calls = []
        self.text = text
        self.stop_reason = stop_reason

    def create(self, **params):
        self.calls.append(params)
        block = SimpleNamespace(type="text", text=self.text, model_dump=lambda: {"type": "text", "text": self.text})
        usage = SimpleNamespace(
            input_tokens=1200, output_tokens=30, cache_creation_input_tokens=1000, cache_read_input_tokens=0
        )
        return SimpleNamespace(
            content=[block],
            usage=usage,
            stop_reason=self.stop_reason,
            model=params["model"],
            _request_id="req_x",
            stop_details=None,
        )


@pytest.fixture
def provider(budget):
    p = ClaudeProvider(get_settings(), budget)
    p.client = SimpleNamespace(messages=FakeMessages())
    return p


def test_rejects_non_haiku_model(budget):
    s = get_settings().model_copy(update={"claude_model": "claude-opus-5-5"})
    with pytest.raises(RuntimeError, match="must be a Haiku"):
        ClaudeProvider(s, budget)


def test_request_shape_caching_effort_and_schema(provider):
    req = LLMRequest(
        step="extract",
        system="SYSTEM",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "t1", "description": "d", "input_schema": {"type": "object"}}],
        output_schema={
            "type": "object",
            "properties": {"a": {"type": "integer"}},
            "required": ["a"],
            "additionalProperties": False,
        },
        max_tokens=100,
    )
    resp = provider.complete(req)
    params = provider.client.messages.calls[0]
    assert params["model"] == "claude-haiku-5-5"
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert params["output_config"]["effort"] == "low"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert "temperature" not in params and "thinking" not in params
    assert resp.parsed == {"a": 1}
    assert resp.usage.cache_write_tokens == 1000 and resp.usage.input_tokens == 1200
    assert resp.cost_usd > 0
    assert provider.budget.summary()["by_step"][0]["step"] == "extract"


def test_haiku_45_omits_effort(budget):
    s = get_settings().model_copy(update={"claude_model": "claude-haiku-4-5"})
    p = ClaudeProvider(s, budget)
    p.client = SimpleNamespace(messages=FakeMessages())
    p.complete(LLMRequest(step="x", messages=[{"role": "user", "content": "hi"}]))
    assert "output_config" not in p.client.messages.calls[0]


def test_budget_blocks_before_call(provider):
    provider.budget.cap = 0
    with pytest.raises(BudgetExceeded):
        provider.complete(LLMRequest(step="x", messages=[{"role": "user", "content": "hi"}]))
    assert provider.client.messages.calls == []


def test_refusal_is_surfaced(provider):
    provider.client = SimpleNamespace(messages=FakeMessages(text="", stop_reason="refusal"))
    resp = provider.complete(
        LLMRequest(step="x", messages=[{"role": "user", "content": "hi"}], output_schema={"type": "object"})
    )
    assert resp.refused and resp.parsed is None
