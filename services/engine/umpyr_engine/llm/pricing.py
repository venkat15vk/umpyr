"""Per-model price table (USD per million tokens) and cost computation.

Sources: Anthropic pricing page as cached in the claude-api reference on 2026-10-06.
Haiku 5.5 has a second rate card above 100K prompt tokens; we never send prompts
that long, and `cost_usd` applies the long-prompt card if it ever happens.
Batch API requests are billed at 50%.

Unknown models fall back to the most expensive Haiku row so we never under-count.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

log = logging.getLogger(__name__)

M = Decimal(1_000_000)


@dataclass(frozen=True)
class ModelPrice:
    model_prefix: str
    input: Decimal
    output: Decimal
    cache_write: Decimal  # 5-minute cache write
    cache_read: Decimal
    long_input: Decimal | None = None  # rate card above long_threshold prompt tokens
    long_output: Decimal | None = None
    long_threshold: int = 100_000


PRICE_TABLE: tuple[ModelPrice, ...] = (
    ModelPrice(
        model_prefix="claude-haiku-5-5",
        input=Decimal("0.10"),
        output=Decimal("0.50"),
        cache_write=Decimal("0.125"),
        cache_read=Decimal("0.01"),
        long_input=Decimal("0.50"),
        long_output=Decimal("2.50"),
    ),
    ModelPrice(
        model_prefix="claude-haiku-4-5",
        input=Decimal("1.00"),
        output=Decimal("5.00"),
        cache_write=Decimal("1.25"),
        cache_read=Decimal("0.10"),
    ),
    # Embedding models (Voyage). Not counted toward the LLM cap but priced for logging.
    ModelPrice(
        model_prefix="voyage-3.5-lite",
        input=Decimal("0.02"),
        output=Decimal(0),
        cache_write=Decimal(0),
        cache_read=Decimal(0),
    ),
    ModelPrice(
        model_prefix="voyage-3.5",
        input=Decimal("0.06"),
        output=Decimal(0),
        cache_write=Decimal(0),
        cache_read=Decimal(0),
    ),
    ModelPrice(
        model_prefix="voyage-3-large",
        input=Decimal("0.18"),
        output=Decimal(0),
        cache_write=Decimal(0),
        cache_read=Decimal(0),
    ),
)

FALLBACK = PRICE_TABLE[1]  # Haiku 4.5 = most expensive Haiku

BATCH_MULTIPLIER = Decimal("0.5")


def price_for(model: str) -> ModelPrice:
    best: ModelPrice | None = None
    for p in PRICE_TABLE:
        if model.startswith(p.model_prefix) and (best is None or len(p.model_prefix) > len(best.model_prefix)):
            best = p
    if best is None:
        log.warning("no price for model %s; using fallback %s", model, FALLBACK.model_prefix)
        return FALLBACK
    return best


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
    is_batch: bool = False,
) -> Decimal:
    p = price_for(model)
    prompt_tokens = input_tokens + cache_write_tokens + cache_read_tokens
    inp, out = p.input, p.output
    if p.long_input is not None and prompt_tokens > p.long_threshold:
        inp, out = p.long_input, p.long_output or p.output
    total = (
        Decimal(input_tokens) * inp
        + Decimal(output_tokens) * out
        + Decimal(cache_write_tokens) * p.cache_write
        + Decimal(cache_read_tokens) * p.cache_read
    ) / M
    if is_batch:
        total *= BATCH_MULTIPLIER
    return total.quantize(Decimal("0.000001"))
