"""PII masking applied on ingest. Only masked text is stored.

Masks: emails, phone numbers, payment card numbers (Luhn-checked), street
addresses, and personal names. Names are the hard part for a regex masker, so
we combine (a) salutation/sign-off patterns, (b) an optional name list (the
synthetic generator records the fake names it used, so masking is verifiable),
and (c) a small built-in list of common first names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
ADDRESS = re.compile(
    r"\b\d{1,6}\s+(?:[A-Z][a-z]+\s){0,3}(?:[A-Z][a-z]+)\s+"
    r"(?:Street|St\.?|Avenue|Ave\.?|Road|Rd\.?|Boulevard|Blvd\.?|Lane|Ln\.?|Drive|Dr\.?|Court|Ct\.?|Way|Place|Pl\.?|Terrace|Circle|Cir\.?)"
    r"(?:,?\s*(?:Apt\.?|Suite|Ste\.?|Unit|#)\s*\w+)?"
    r"(?:,\s*[A-Z][a-zA-Z .]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)?\b"
)
SALUTATION = re.compile(
    r"\b(Hi|Hello|Hey|Dear|Thanks|Thank you|Regards|Best|Cheers|Sincerely|Best regards|Kind regards|Warm regards)"
    r"(,)?\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)(?=[,.!\n]|\s*$)"
)
NAME_TOKEN = "[NAME]"

COMMON_FIRST_NAMES = {
    "james",
    "mary",
    "john",
    "patricia",
    "robert",
    "jennifer",
    "michael",
    "linda",
    "william",
    "elizabeth",
    "david",
    "barbara",
    "richard",
    "susan",
    "joseph",
    "jessica",
    "thomas",
    "sarah",
    "charles",
    "karen",
    "christopher",
    "lisa",
    "daniel",
    "nancy",
    "matthew",
    "betty",
    "anthony",
    "sandra",
    "mark",
    "margaret",
    "donald",
    "ashley",
    "steven",
    "kimberly",
    "paul",
    "emily",
    "andrew",
    "donna",
    "joshua",
    "michelle",
    "kenneth",
    "carol",
    "kevin",
    "amanda",
    "brian",
    "melissa",
    "george",
    "deborah",
    "timothy",
    "stephanie",
    "ronald",
    "rebecca",
    "jason",
    "sharon",
    "edward",
    "laura",
    "jeffrey",
    "cynthia",
    "ryan",
    "dorothy",
    "jacob",
    "amy",
    "gary",
    "kathleen",
    "nicholas",
    "angela",
    "eric",
    "shirley",
    "jonathan",
    "brenda",
    "stephen",
    "emma",
    "larry",
    "anna",
    "justin",
    "pamela",
    "scott",
    "nicole",
    "brandon",
    "samantha",
    "benjamin",
    "katherine",
    "samuel",
    "christine",
    "gregory",
    "helen",
    "alexander",
    "debra",
    "frank",
    "rachel",
    "patrick",
    "carolyn",
    "raymond",
    "janet",
    "jack",
    "maria",
    "dennis",
    "olivia",
    "jerry",
    "heather",
    "tyler",
    "priya",
    "wei",
    "carlos",
    "sofia",
    "ahmed",
    "fatima",
    "hiroshi",
    "yuki",
    "omar",
    "leila",
}


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass
class MaskResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    def bump(self, kind: str, n: int = 1) -> None:
        self.counts[kind] = self.counts.get(kind, 0) + n


class PIIMasker:
    def __init__(self, known_names: list[str] | None = None, extra_first_names: set[str] | None = None):
        names = sorted({n.strip() for n in (known_names or []) if n and n.strip()}, key=len, reverse=True)
        self.known_names_re = re.compile(r"\b(" + "|".join(re.escape(n) for n in names) + r")\b") if names else None
        self.first_names = COMMON_FIRST_NAMES | {n.lower() for n in (extra_first_names or set())}
        self.first_name_re = re.compile(
            r"\b(" + "|".join(sorted(re.escape(n) for n in self.first_names)) + r")(\s+[A-Z][a-z]{1,20})?\b",
            re.IGNORECASE,
        )

    def mask(self, text: str | None) -> MaskResult:
        if not text:
            return MaskResult(text or "")
        res = MaskResult(text)

        def sub(pattern: re.Pattern[str], repl, kind: str) -> None:
            new, n = pattern.subn(repl, res.text)
            if n:
                res.text = new
                res.bump(kind, n)

        sub(EMAIL, "[EMAIL]", "email")

        def card_repl(m: re.Match[str]) -> str:
            digits = re.sub(r"\D", "", m.group(0))
            return "[CARD]" if 13 <= len(digits) <= 19 and _luhn_ok(digits) else m.group(0)

        sub(CARD, card_repl, "card")
        sub(PHONE, "[PHONE]", "phone")
        sub(ADDRESS, "[ADDRESS]", "address")
        if self.known_names_re is not None:
            sub(self.known_names_re, NAME_TOKEN, "name")
        sub(SALUTATION, lambda m: f"{m.group(1)}{m.group(2) or ''} {NAME_TOKEN}", "name")

        def first_repl(m: re.Match[str]) -> str:
            word = m.group(1)
            # Only mask when capitalised (a name), not e.g. "mark the box" or "will".
            if not word[0].isupper():
                return m.group(0)
            return NAME_TOKEN

        sub(self.first_name_re, first_repl, "name")
        res.text = re.sub(r"\[NAME\](?:\s+\[NAME\])+", NAME_TOKEN, res.text)
        return res


DEFAULT_MASKER = PIIMasker()


def mask_text(text: str | None, masker: PIIMasker | None = None) -> str:
    return (masker or DEFAULT_MASKER).mask(text).text
