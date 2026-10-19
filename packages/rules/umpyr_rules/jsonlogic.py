"""A small deterministic JSON Logic evaluator.

Supported operators: var, missing, missing_some, if, ==, ===, !=, !==, !, !!,
and, or, <, <=, >, >=, +, -, *, /, %, min, max, in, cat, merge, some, all, none,
map, filter, reduce, substr, log.

Design choices (see docs/DECISIONS.md):
- Numbers are coerced to Decimal so amounts are exact and reproducible.
- Comparison between a Decimal and an int/float works; strings compare as strings.
- `var` on a missing path returns None (JSON Logic semantics) - the engine uses
  `requires` + `missing` to decide when a fact must be asked for.
- No side effects, no randomness, no clock access. Same input -> same output.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

Json = Any


class JsonLogicError(ValueError):
    pass


def _is_logic(obj: Any) -> bool:
    return isinstance(obj, dict) and len(obj) == 1


def _num(value: Any) -> Decimal:
    if isinstance(value, bool):
        return Decimal(1 if value else 0)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.strip())
        except InvalidOperation as e:
            raise JsonLogicError(f"not a number: {value!r}") from e
    if value is None:
        return Decimal(0)
    raise JsonLogicError(f"not a number: {value!r}")


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        return value != 0
    if isinstance(value, (str, list, dict)):
        return len(value) > 0
    return bool(value)


def _loose_eq(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return _truthy(a) == _truthy(b)
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        return _num(a) == _num(b)
    if isinstance(a, (int, float, Decimal)) and isinstance(b, str):
        try:
            return _num(a) == _num(b)
        except JsonLogicError:
            return False
    if isinstance(b, (int, float, Decimal)) and isinstance(a, str):
        try:
            return _num(a) == _num(b)
        except JsonLogicError:
            return False
    return a == b


def _strict_eq(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float, Decimal)) and isinstance(b, (int, float, Decimal)):
        return _num(a) == _num(b)
    return type(a) is type(b) and a == b


def _cmp(op: str, values: list[Any]) -> bool:
    """Chained comparisons: {"<": [1, x, 10]} means 1 < x < 10."""
    if len(values) < 2:
        raise JsonLogicError(f"{op} needs at least 2 operands")
    for a, b in zip(values, values[1:], strict=False):
        if a is None or b is None:
            return False
        if isinstance(a, str) and isinstance(b, str):
            left, right = a, b
        else:
            left, right = _num(a), _num(b)
        ok = {
            "<": left < right,
            "<=": left <= right,
            ">": left > right,
            ">=": left >= right,
        }[op]
        if not ok:
            return False
    return True


def get_var(data: Any, path: str | int | None, default: Any = None) -> Any:
    if path is None or path == "":
        return data
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return default
        else:
            return default
        if cur is None:
            return default
    return cur


def collect_vars(logic: Json, out: set[str] | None = None) -> set[str]:
    """Every `var` path referenced in a logic tree (used to validate rules against the catalog)."""
    if out is None:
        out = set()
    if _is_logic(logic):
        op, args = next(iter(logic.items()))
        if not isinstance(args, list):
            args = [args]
        if op == "var":
            if args and isinstance(args[0], str):
                out.add(args[0])
            elif args and _is_logic(args[0]):
                collect_vars(args[0], out)
            return out
        if op in ("missing", "missing_some"):
            for a in args:
                if isinstance(a, str):
                    out.add(a)
                elif isinstance(a, list):
                    out.update(x for x in a if isinstance(x, str))
                elif _is_logic(a):
                    collect_vars(a, out)
            return out
        for a in args:
            collect_vars(a, out)
    elif isinstance(logic, list):
        for a in logic:
            collect_vars(a, out)
    return out


def apply(logic: Json, data: Any = None) -> Any:  # noqa: C901 - one big dispatch is clearest here
    if isinstance(logic, list):
        return [apply(x, data) for x in logic]
    if not _is_logic(logic):
        return logic
    op, raw_args = next(iter(logic.items()))
    args = raw_args if isinstance(raw_args, list) else [raw_args]

    # --- operators that must not pre-evaluate all arguments ---
    if op == "var":
        path = apply(args[0], data) if args else None
        default = apply(args[1], data) if len(args) > 1 else None
        return get_var(data, path, default)
    if op == "missing":
        keys = apply(args[0], data) if len(args) == 1 and _is_logic(args[0]) else args
        if isinstance(keys, list) and len(keys) == 1 and isinstance(keys[0], list):
            keys = keys[0]
        return [k for k in keys if get_var(data, k, None) is None]
    if op == "missing_some":
        need = int(apply(args[0], data))
        keys = apply(args[1], data)
        missing = [k for k in keys if get_var(data, k, None) is None]
        present = len(keys) - len(missing)
        return [] if present >= need else missing
    if op == "if" or op == "?:":
        i = 0
        while i < len(args) - 1:
            if _truthy(apply(args[i], data)):
                return apply(args[i + 1], data)
            i += 2
        return apply(args[i], data) if i < len(args) else None
    if op == "and":
        result: Any = True
        for a in args:
            result = apply(a, data)
            if not _truthy(result):
                return result
        return result
    if op == "or":
        result = False
        for a in args:
            result = apply(a, data)
            if _truthy(result):
                return result
        return result
    if op in ("some", "all", "none"):
        items = apply(args[0], data)
        if not isinstance(items, list):
            items = []
        test = args[1]
        hits = [bool(_truthy(apply(test, it))) for it in items]
        if op == "some":
            return any(hits)
        if op == "all":
            return bool(items) and all(hits)
        return not any(hits)
    if op == "map":
        items = apply(args[0], data) or []
        return [apply(args[1], it) for it in items]
    if op == "filter":
        items = apply(args[0], data) or []
        return [it for it in items if _truthy(apply(args[1], it))]
    if op == "reduce":
        items = apply(args[0], data) or []
        acc = apply(args[2], data) if len(args) > 2 else None
        for it in items:
            acc = apply(args[1], {"current": it, "accumulator": acc})
        return acc

    # --- eager operators ---
    vals = [apply(a, data) for a in args]
    if op == "==":
        return _loose_eq(vals[0], vals[1])
    if op == "===":
        return _strict_eq(vals[0], vals[1])
    if op == "!=":
        return not _loose_eq(vals[0], vals[1])
    if op == "!==":
        return not _strict_eq(vals[0], vals[1])
    if op == "!":
        return not _truthy(vals[0])
    if op == "!!":
        return _truthy(vals[0])
    if op in ("<", "<=", ">", ">="):
        return _cmp(op, vals)
    if op == "+":
        return sum((_num(v) for v in vals), Decimal(0))
    if op == "-":
        if len(vals) == 1:
            return -_num(vals[0])
        return _num(vals[0]) - _num(vals[1])
    if op == "*":
        result = Decimal(1)
        for v in vals:
            result *= _num(v)
        return result
    if op == "/":
        divisor = _num(vals[1])
        if divisor == 0:
            raise JsonLogicError("division by zero")
        return _num(vals[0]) / divisor
    if op == "%":
        return _num(vals[0]) % _num(vals[1])
    if op == "min":
        return min(_num(v) for v in vals)
    if op == "max":
        return max(_num(v) for v in vals)
    if op == "in":
        needle, hay = vals[0], vals[1]
        if hay is None:
            return False
        if isinstance(hay, str):
            return str(needle) in hay
        return any(_loose_eq(needle, h) for h in hay)
    if op == "cat":
        return "".join("" if v is None else str(v) for v in vals)
    if op == "merge":
        out: list[Any] = []
        for v in vals:
            out.extend(v if isinstance(v, list) else [v])
        return out
    if op == "substr":
        s = str(vals[0])
        start = int(vals[1])
        if len(vals) > 2:
            length = int(vals[2])
            return s[start : start + length] if length >= 0 else s[start:length]
        return s[start:]
    if op == "log":
        return vals[0]
    raise JsonLogicError(f"unknown operator: {op}")
