"""Clause provenance: locating clauses, sections, pages, hashes, citations. No model calls, no database."""

from __future__ import annotations

from umpyr_engine.compile.provenance import (
    attach_provenance,
    citation,
    clause_columns,
    headings,
    locate,
    page_for,
    section_for,
    text_hash,
)
from umpyr_rules import Clause, Provenance

DOC = """Rule 1. Definitions
Some words about definitions that run on for a line.

Rule 7. Refunds
A Credit must be used for travel booked   and flown within one (1) year
from date of issuance. Credits are non-transferable.

Rule 8. Baggage
Checked bags arrive within 20 minutes of the aircraft parking at the gate.
"""


def test_locate_exact_whitespace_and_anchor():
    assert locate(DOC, "Rule 8. Baggage") == (DOC.index("Rule 8"), DOC.index("Rule 8") + len("Rule 8. Baggage"))
    ws = locate(DOC, "A Credit must be used for travel booked and flown within one (1) year from date of issuance.")
    assert ws is not None and DOC[ws[0] : ws[0] + 8] == "A Credit"
    anchor = locate(DOC, "Checked bags arrive within 20 minutes of the aircraft parking at the gate, said someone else")
    assert anchor is not None and DOC[anchor[0] : anchor[0] + 12] == "Checked bags"
    assert locate(DOC, "This sentence is nowhere in the document") is None
    assert locate(DOC, "   ") is None


def test_headings_and_sections():
    heads = headings(DOC)
    names = [h for _, h in heads]
    assert names[:3] == ["Rule 1. Definitions", "Rule 7. Refunds", "Rule 8. Baggage"]
    assert section_for(heads, DOC.index("A Credit")) == "Rule 7. Refunds"
    assert section_for(heads, DOC.index("Checked bags")) == "Rule 8. Baggage"
    assert section_for(heads, 0) == "Rule 1. Definitions"


def test_text_hash_normalises_whitespace():
    assert text_hash("a  b\n c") == text_hash("a b c") and text_hash("a b c") != text_hash("a b d")


def test_page_for():
    pages = ["Rule 1. Definitions\nwords", "Rule 7. Refunds\nA Credit must be used for travel", "Rule 8. Baggage"]
    assert page_for(pages, "A Credit must be used for travel") == 2
    assert page_for(pages, "Rule 8. Baggage") == 3
    assert page_for(pages, "nowhere at all in these pages") is None


def test_attach_provenance_and_columns():
    docs = [
        {"source_id": "coc", "title": "Contract", "text": DOC, "sha256": "f" * 64, "content_type": "text/html"},
    ]
    clauses = [
        {"clause_id": "T-REF-1", "source_id": "coc", "title": "Credit", "text": "Credits are non-transferable."},
        {"clause_id": "T-X-1", "source_id": "missing", "title": "Orphan", "text": "No document", "source_url": "u"},
    ]
    report = attach_provenance(clauses, docs, None)
    p = clauses[0]["provenance"]
    assert (
        p["located"] and p["section"] == "Rule 7. Refunds" and p["page"] is None and p["document_version"] == "f" * 64
    )
    assert p["span"][0] == DOC.index("Credits are") and p["public"] is True
    assert clauses[1]["provenance"]["located"] is False and report["unlocated"] == ["T-X-1"]
    cols = clause_columns(clauses[0])
    assert cols["span_start"] == p["span"][0] and cols["text_hash"] == text_hash("Credits are non-transferable.")
    assert cols["public"] is True


def test_citation_from_clause_model_and_practice_is_private():
    prov = Provenance(
        document_id="coc",
        document_title="Contract of Carriage",
        document_version="a" * 64,
        section="Rule 7",
        page=12,
        span=[10, 40],
        text_hash="h" * 64,
    )
    c = Clause(
        clause_id="AK-REF-1",
        title="t",
        text="Credits are non-transferable.",
        source_url="https://x/coc",
        tier="contract",
        provenance=prov,
    )
    cit = citation(c)
    assert cit["document"] == "Contract of Carriage" and cit["page"] == 12 and cit["section"] == "Rule 7"
    assert cit["document_version"] == "a" * 12 and cit["public"] is True and cit["quote"].startswith("Credits")
    practice = Clause(
        clause_id="PRACTICE-1", title="p", text="we always refund", source_url="practice://ws/1", tier="goodwill"
    )
    assert citation(practice)["public"] is False and citation(practice)["text_hash"] == text_hash("we always refund")
