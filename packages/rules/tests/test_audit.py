from datetime import UTC, datetime
from decimal import Decimal

from umpyr_rules import Ruling, audit_hash, canonical_json


def test_canonical_json_sorts_keys_and_formats_decimals():
    a = canonical_json({"b": Decimal("1.10"), "a": [Decimal("2"), None], "t": datetime(2026, 1, 1, tzinfo=UTC)})
    assert a == '{"a":["2",null],"b":"1.10","t":"2026-01-01T00:00:00+00:00"}'
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_hash_stable_and_sensitive():
    rec = {"ticket_id": "T1", "ruling": Ruling(decision="approve", rule_pack_version="v1"), "facts": {"x": 1}}
    h1 = audit_hash(rec)
    assert len(h1) == 64 and h1 == audit_hash(rec)
    rec["facts"]["x"] = 2
    assert audit_hash(rec) != h1
