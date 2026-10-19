"""Field catalog: the complete list of paths a rule or procedure may reference.

Derived from the Pydantic models so it can never drift from them. The compiler
shows this catalog to Claude and the rule validator rejects any `var` that is
not in it.
"""

from __future__ import annotations

import types
import typing
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel

from . import models

ROOTS: dict[str, type[BaseModel]] = {
    "customer": models.Customer,
    "order": models.Order,
    "item": models.Item,
    "shipment": models.Shipment,
    "booking": models.Booking,
    "flight": models.Flight,
    "bag": models.Bag,
    "claim": models.Claim,
    "case": models.Case,
}


@dataclass(frozen=True)
class FieldSpec:
    path: str
    type: str
    description: str
    enum_values: tuple[str, ...] = ()


def _type_name(annotation) -> tuple[str, tuple[str, ...]]:
    origin = typing.get_origin(annotation)
    if origin is types.UnionType or origin is typing.Union:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _type_name(args[0])
        return ("union", ())
    if origin is list:
        inner = typing.get_args(annotation)[0]
        return (f"list[{_type_name(inner)[0]}]", ())
    if isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return ("enum", tuple(e.value for e in annotation))
        if annotation is bool:
            return ("bool", ())
        if annotation is int:
            return ("int", ())
        if annotation is float:
            return ("number", ())
        if annotation is Decimal:
            return ("decimal", ())
        if annotation is str:
            return ("string", ())
        if annotation is datetime:
            return ("datetime", ())
        if issubclass(annotation, BaseModel):
            return (annotation.__name__, ())
    return (str(annotation), ())


def _walk(prefix: str, model: type[BaseModel], out: list[FieldSpec]) -> None:
    for name, field in model.model_fields.items():
        path = f"{prefix}.{name}"
        tname, enum_values = _type_name(field.annotation)
        desc = field.description or ""
        out.append(FieldSpec(path=path, type=tname, description=desc, enum_values=enum_values))
        # Nested models that are not lists are walked; lists are leaf collections (rules use `some`/`all`).
        ann = field.annotation
        origin = typing.get_origin(ann)
        if origin is types.UnionType or origin is typing.Union:
            inner = [a for a in typing.get_args(ann) if a is not type(None)]
            ann = inner[0] if len(inner) == 1 else None
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            _walk(path, ann, out)


def build_catalog() -> list[FieldSpec]:
    out: list[FieldSpec] = []
    for root, model in ROOTS.items():
        _walk(root, model, out)
    return out


FIELD_CATALOG: list[FieldSpec] = build_catalog()
_PATHS: frozenset[str] = frozenset(f.path for f in FIELD_CATALOG)


def field_paths() -> frozenset[str]:
    return _PATHS


def validate_path(path: str) -> bool:
    """True if `path` is a catalog path. Paths into list elements (order.items.0.price) are allowed
    when the element type's fields exist; we check by stripping numeric segments."""
    if path in _PATHS:
        return True
    parts = path.split(".")
    stripped = [p for p in parts if not p.isdigit()]
    candidate = ".".join(stripped)
    if candidate in _PATHS:
        return True
    # list element fields: order.items.price -> look up via element model
    root = ROOTS.get(parts[0])
    if root is None:
        return False
    model: type[BaseModel] | None = root
    for seg in parts[1:]:
        if seg.isdigit():
            continue
        if model is None or seg not in model.model_fields:
            return False
        ann = model.model_fields[seg].annotation
        origin = typing.get_origin(ann)
        if origin is types.UnionType or origin is typing.Union:
            inner = [a for a in typing.get_args(ann) if a is not type(None)]
            ann = inner[0] if len(inner) == 1 else None
            origin = typing.get_origin(ann)
        if origin is list:
            ann = typing.get_args(ann)[0]
        model = ann if isinstance(ann, type) and issubclass(ann, BaseModel) else None
    return True


def catalog_markdown() -> str:
    """Compact catalog text for prompts."""
    lines = []
    for f in FIELD_CATALOG:
        if f.type.startswith("list[") or f.type[0].isupper():
            continue
        extra = f" one of: {', '.join(f.enum_values)}" if f.enum_values else ""
        lines.append(f"- {f.path} ({f.type}){extra} — {f.description}")
    return "\n".join(lines)
