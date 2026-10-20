"""Clause provenance: locate every compiled clause in its archived source document.

No model calls. For each clause we record the document version (sha256 of the fetched bytes), the nearest
heading above it, the PDF page (when the source is a PDF), the character span in the archived text, a hash of
the normalised clause text, and whether the document is public (customers may be shown it).
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STRONG_HEADING_RE = re.compile(r"^(?:rule|section|sec\.|part|article|chapter|appendix|§)\s*\d+[\w.()-]*(?:\s|$)", re.I)
LETTERED_RE = re.compile(r"^(?:[A-Z]|[ivx]+|\d{1,2})[.)]\s+[A-Z][\w'/& -]{2,40}$")
NUMBERED_RE = re.compile(r"^\d+(?:\.\d+)+\.?\s+[A-Z][\w'/& -]{2,40}$")
FAQ_RE = re.compile(r"^Q:\s+\S")
SMALL_WORDS = {"a", "an", "and", "of", "the", "to", "for", "in", "on", "or", "at", "by", "with", "from", "vs", "per"}
FOLD = str.maketrans(
    {"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00a0": " "}
)


def fold(text: str) -> str:
    """Map curly quotes, dashes and non-breaking spaces to ASCII (1:1, so offsets are unchanged)."""
    return text.translate(FOLD)


def normalise(text: str) -> str:
    return " ".join(fold(text).split())


def text_hash(text: str) -> str:
    return hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()


def _ws_pattern(text: str, max_tokens: int | None = None) -> re.Pattern[str]:
    tokens = normalise(text).split(" ")
    if max_tokens:
        tokens = tokens[:max_tokens]
    return re.compile(r"\s+".join(re.escape(t) for t in tokens))


def locate(doc_text: str, clause_text: str) -> tuple[int, int] | None:
    """Find the clause in the document: exact, then whitespace-insensitive, then by its first words."""
    if not clause_text.strip():
        return None
    doc_text, clause_text = fold(doc_text), fold(clause_text)
    i = doc_text.find(clause_text)
    if i >= 0:
        return i, i + len(clause_text)
    m = _ws_pattern(clause_text).search(doc_text)
    if m:
        return m.start(), m.end()
    m = _ws_pattern(clause_text, max_tokens=8).search(doc_text)
    if m:
        return m.start(), min(len(doc_text), m.start() + len(normalise(clause_text)))
    return None


def _is_heading(line: str) -> bool:
    """Section headings: 'Rule 7. Refunds', '§ 260.6 Refunds', 'A. Involuntary Refunds', '1.2 Title', 'Q: ...',
    or a short Title Case line that does not end mid-sentence."""
    s = fold(line).strip()
    if not 3 <= len(s) <= 100:
        return False
    if STRONG_HEADING_RE.match(s) or FAQ_RE.match(s) or LETTERED_RE.match(s) or NUMBERED_RE.match(s):
        return True
    if s[-1] in ".,;:" or s.lower().endswith((" and", " or", " of", " the", " to", " a")):
        return False
    words = s.split()
    alpha = [w for w in words if w[:1].isalpha()]
    return (
        2 <= len(words) <= 8
        and len(alpha) >= 2
        and s[0].isupper()
        and all(w[:1].isupper() or w.lower() in SMALL_WORDS for w in alpha)
    )


def headings(doc_text: str) -> list[tuple[int, str]]:
    """(offset, heading) for every line that looks like a section heading."""
    out: list[tuple[int, str]] = []
    pos = 0
    for line in doc_text.split("\n"):
        if _is_heading(line):
            out.append((pos, " ".join(line.split())[:100]))
        pos += len(line) + 1
    return out


def section_for(heads: list[tuple[int, str]], offset: int) -> str | None:
    best = None
    for pos, h in heads:
        if pos <= offset:
            best = h
        else:
            break
    return best


def pdf_pages(raw_path: Path) -> list[str]:
    import pymupdf

    with pymupdf.open(str(raw_path)) as doc:
        return [page.get_text() for page in doc]


def page_for(pages: list[str], clause_text: str) -> int | None:
    """1-based page holding the clause (first words), searching whole pages then page boundaries."""
    pat = _ws_pattern(clause_text, max_tokens=8)
    for n, t in enumerate(pages, start=1):
        if pat.search(t):
            return n
    joined = "\n".join(pages)
    m = pat.search(joined)
    if not m:
        return None
    pos = 0
    for n, t in enumerate(pages, start=1):
        pos += len(t) + 1
        if m.start() < pos:
            return n
    return None


def attach_provenance(
    clauses: list[dict[str, Any]], docs: list[dict[str, Any]], policy_dir: Path | None = None
) -> dict:
    """Add a `provenance` dict to every clause in place. Returns a small report."""
    by_id = {d["source_id"]: d for d in docs}
    heads = {sid: headings(d["text"]) for sid, d in by_id.items()}
    pages: dict[str, list[str]] = {}
    report = {"clauses": len(clauses), "located": 0, "paged": 0, "sectioned": 0, "unlocated": []}
    for c in clauses:
        sid = c.get("source_id") or ""
        d = by_id.get(sid)
        if d is None:
            c["provenance"] = {
                "document_id": sid or "unknown",
                "document_title": c.get("source_title"),
                "document_version": c.get("document_sha256") or "0" * 64,
                "section": None,
                "page": None,
                "span": None,
                "text_hash": text_hash(c["text"]),
                "public": bool(c.get("public", not str(c.get("source_url", "")).startswith("practice://"))),
                "located": False,
            }
            report["unlocated"].append(c["clause_id"])
            continue
        span = locate(d["text"], c["text"])
        section = section_for(heads[sid], span[0]) if span else None
        page = None
        if d.get("content_type") == "application/pdf" and d.get("raw_path") and policy_dir is not None:
            if sid not in pages:
                p = policy_dir / d["raw_path"]
                pages[sid] = pdf_pages(p) if p.exists() else []
            if pages[sid]:
                page = page_for(pages[sid], c["text"])
        c["provenance"] = {
            "document_id": sid,
            "document_title": d.get("title"),
            "document_version": d["sha256"],
            "section": section,
            "page": page,
            "span": list(span) if span else None,
            "text_hash": text_hash(c["text"]),
            "public": bool(d.get("public", True)),
            "located": span is not None,
        }
        report["located"] += span is not None
        report["paged"] += page is not None
        report["sectioned"] += section is not None
        if span is None:
            report["unlocated"].append(c["clause_id"])
    return report


def clause_columns(c: dict[str, Any]) -> dict[str, Any]:
    """Provenance columns for a `clauses` row from a clause dict (missing provenance -> hash only)."""
    p = c.get("provenance") or {}
    span = p.get("span") or [None, None]
    return {
        "document_version": p.get("document_version"),
        "section": p.get("section"),
        "page": p.get("page"),
        "span_start": span[0],
        "span_end": span[1],
        "text_hash": p.get("text_hash") or text_hash(c["text"]),
        "public": bool(p.get("public", True)),
    }


def citation(clause: Any, quote_chars: int = 300) -> dict[str, Any]:
    """Citation payload for a `umpyr_rules.Clause` (or dict with the same fields). Never model-generated."""
    get = (lambda k: clause.get(k)) if isinstance(clause, dict) else (lambda k: getattr(clause, k, None))
    prov = get("provenance")
    if prov is not None and not isinstance(prov, dict):
        prov = prov.model_dump()
    prov = prov or {}
    text = get("text") or ""
    return {
        "clause_id": get("clause_id"),
        "title": get("title"),
        "document": prov.get("document_title") or get("source_title") or prov.get("document_id") or get("source_url"),
        "document_id": prov.get("document_id"),
        "document_version": (prov.get("document_version") or "")[:12] or None,
        "section": prov.get("section"),
        "page": prov.get("page"),
        "span": prov.get("span"),
        "text_hash": prov.get("text_hash") or text_hash(text),
        "url": get("source_url"),
        "tier": str(get("tier")) if get("tier") is not None else None,
        "public": bool(prov.get("public", not str(get("source_url") or "").startswith("practice://"))),
        "quote": text[:quote_chars],
    }
