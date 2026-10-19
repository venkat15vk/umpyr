"""Synthetic data core: deterministic fake PII, people, timestamps and the ticket/truth records.

Everything is seeded so a rebuild reproduces the same orders and scenarios; only the LLM-written
thread text varies. All names, emails, phones and addresses are fake (555 numbers, example domains).
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

NOW = datetime(2026, 10, 1, 12, 0, 0)  # fixed "today" for derived day counts

FIRST = [
    "Priya",
    "Marcus",
    "Elena",
    "Tomas",
    "Aisha",
    "Daniel",
    "Mei",
    "Jonah",
    "Sofia",
    "Victor",
    "Hannah",
    "Omar",
    "Grace",
    "Leo",
    "Nadia",
    "Caleb",
    "Ingrid",
    "Rafael",
    "Yuki",
    "Gabriel",
    "Leila",
    "Ethan",
    "Chloe",
    "Mateo",
    "Fatima",
    "Owen",
    "Renata",
    "Samuel",
    "Dana",
    "Hiroshi",
    "Noor",
    "Patrick",
    "Beatriz",
    "Kenji",
    "Lucia",
    "Wes",
    "Amara",
    "Felix",
    "Rosa",
    "Dmitri",
    "Tessa",
    "Arjun",
    "Maya",
    "Niall",
    "Simone",
    "Ravi",
    "Greta",
    "Luis",
]
LAST = [
    "Natarajan",
    "Delacroix",
    "Okafor",
    "Lindqvist",
    "Haddad",
    "Moreau",
    "Tanaka",
    "Whitfield",
    "Castellano",
    "Brennan",
    "Novak",
    "Rahman",
    "Ibarra",
    "Schultz",
    "Kowalski",
    "Ferreira",
    "Osei",
    "Marchetti",
    "Dubois",
    "Nakamura",
    "Abara",
    "Petrov",
    "Sandoval",
    "Halvorsen",
    "Mbeki",
    "Quintero",
    "Fitzgerald",
    "Varga",
    "Santos",
    "Yilmaz",
    "Ortega",
    "Bauer",
]
STREETS = [
    "Maple Street",
    "Cedar Avenue",
    "Birch Lane",
    "Oak Road",
    "Willow Drive",
    "Pine Court",
    "Elm Place",
    "Aspen Way",
]
CITIES = [
    ("Minneapolis", "MN", "55401"),
    ("Seattle", "WA", "98101"),
    ("Portland", "OR", "97201"),
    ("Denver", "CO", "80202"),
    ("Austin", "TX", "78701"),
    ("Columbus", "OH", "43215"),
    ("Raleigh", "NC", "27601"),
    ("Phoenix", "AZ", "85001"),
    ("Anchorage", "AK", "99501"),
    ("San Jose", "CA", "95110"),
    ("Boise", "ID", "83702"),
    ("Spokane", "WA", "99201"),
]
DOMAINS = ["example.com", "example.net", "example.org", "mail.example.com"]


@dataclass
class Person:
    first: str
    last: str
    email: str
    phone: str
    address: str
    customer_id: str

    @property
    def name(self) -> str:
        return f"{self.first} {self.last}"


def make_person(rng: random.Random, idx: int) -> Person:
    first, last = rng.choice(FIRST), rng.choice(LAST)
    city, state, zipc = rng.choice(CITIES)
    email = f"{first.lower()}.{last.lower()}{rng.randint(1, 99)}@{rng.choice(DOMAINS)}"
    phone = f"({rng.randint(201, 989)}) 555-{rng.randint(1000, 9999):04d}"
    address = f"{rng.randint(10, 9999)} {rng.choice(STREETS)}, {city}, {state} {zipc}"
    return Person(first, last, email, phone, address, f"C-{10000 + idx}")


def money(x: float | Decimal) -> Decimal:
    return Decimal(str(round(float(x), 2))).quantize(Decimal("0.01"))


def iso(dt: datetime | None) -> str | None:
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None


@dataclass
class StaffStep:
    action: str
    detail: str = ""
    amount: Decimal | None = None


@dataclass
class Scenario:
    """A fully specified synthetic case before the LLM writes the thread."""

    ticket_id: str
    category_key: str
    claim_reason: str
    person: Person
    order_id: str
    created_at: datetime
    facts: dict[str, Any]  # ontology Facts dict used by the rule engine
    issue_brief: str  # what the customer is writing about, for the LLM
    customer_asks: str  # what they want
    policy: dict[str, Any]  # engine ruling summary
    staff_outcome: dict[str, Any]  # decision/remedy/amount/fault actually given
    staff_steps: list[StaffStep]
    hours_to_resolve: float
    is_drift: bool = False
    drift_reason: str | None = None
    channel: str = "email"
    extra_context: dict[str, Any] = field(default_factory=dict)

    def to_truth(self) -> dict[str, Any]:
        d = asdict(self)
        d["person"] = {"customer_id": self.person.customer_id, "name": self.person.name}
        d["created_at"] = iso(self.created_at)
        d["staff_steps"] = [
            {"action": s.action, "detail": s.detail, "amount": str(s.amount) if s.amount is not None else None}
            for s in self.staff_steps
        ]
        return json.loads(json.dumps(d, default=str))


def thread_timestamps(rng: random.Random, start: datetime, n_messages: int, hours_total: float) -> list[datetime]:
    """Spread n messages across hours_total, first at start."""
    if n_messages <= 1:
        return [start]
    cuts = sorted(rng.random() for _ in range(n_messages - 1))
    return [start] + [start + timedelta(hours=hours_total * c) for c in cuts]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
