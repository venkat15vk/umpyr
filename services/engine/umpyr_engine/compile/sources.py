"""Fetch and archive public policy sources.

For every source we keep: the raw bytes (HTML/PDF/XML), the extracted text, and a
meta JSON with URL, final URL, content type, SHA-256 and fetch date. Nothing is
summarised at this stage; the compiler works from the archived text.

Extractors
- PDF            -> PyMuPDF
- eCFR URLs      -> eCFR versioner API (XML), flattened to text
- Alaska pages   -> Next.js `__NEXT_DATA__` payload (their CMS content is embedded there;
                    the DOM itself is rendered client-side)
- other HTML     -> trafilatura
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


@dataclass
class SourceDoc:
    source_id: str
    url: str
    final_url: str
    content_type: str
    raw: bytes
    text: str
    title: str
    fetched_at: str
    sha256: str
    extractor: str
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.text.strip()) > 200


def _strip_html(html: str) -> str:
    html = re.sub(r"<(br|/p|/li|/h[1-6]|/tr|/div)[^>]*>", "\n", html, flags=re.I)
    html = re.sub(r"<li[^>]*>", "- ", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    text = unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def _next_data_text(html: str) -> tuple[str, str] | None:
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    props = data.get("props", {}).get("pageProps", {}) or {}
    if not props:
        return None
    title = ""
    head = props.get("head_metadata") or {}
    if isinstance(head, dict):
        title = head.get("title") or head.get("page_title") or ""
    SKIP_KEYS = {"navigation", "header_configuration", "footer", "ACL", "tags", "head_metadata", "_metadata"}
    NOISE_HEADINGS = {"JSON GTR Attribute Collection", "BorealisJsonGtr"}
    ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")
    out: list[str] = []

    def walk(node, key=""):
        if isinstance(node, dict):
            for k, v in node.items():
                if (
                    k in SKIP_KEYS
                    or k.startswith("_")
                    or k in ("uid", "locale", "created_by", "updated_by", "url", "href")
                ):
                    continue
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, str):
            s = node.strip()
            if len(s) < 3 or s.startswith("blt") or s.startswith("http") or ISO_TS.match(s):
                return
            if key in ("title", "heading", "header", "subtitle", "label", "name", "question"):
                if s in NOISE_HEADINGS:
                    return
                out.append(f"\n## {_strip_html(s)}\n")
            elif len(s) > 20 or "<" in s:
                out.append(_strip_html(s))

    walk(props)
    text = "\n".join(x for x in out if x.strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text


def _ecfr_api_url(url: str) -> str | None:
    """Map an ecfr.gov page URL to the versioner API URL for that part."""
    u = urlparse(url)
    if "ecfr.gov" not in u.netloc:
        return None
    if "/api/" in u.path:
        return url
    m = re.search(r"/title-(\d+)/.*part-(\d+)", u.path)
    if not m:
        return None
    title, part = m.group(1), m.group(2)
    date = datetime.now(UTC).date().replace(day=1).isoformat()
    return f"https://www.ecfr.gov/api/versioner/v1/full/{date}/title-{title}.xml?part={part}"


def _ecfr_xml_text(xml: str) -> tuple[str, str]:
    from lxml import etree

    root = etree.fromstring(xml.encode("utf-8"))
    title = (root.findtext("HEAD") or "").strip()
    parts: list[str] = []
    for el in root.iter():
        if el.tag == "HEAD":
            parts.append(f"\n## {(el.text or '').strip()}\n")
        elif el.tag in ("P", "FP", "PSPACE"):
            txt = "".join(el.itertext()).strip()
            if txt:
                parts.append(txt)
    return title, "\n".join(parts)


def fetch_source(source_id: str, url: str, timeout: float = 60.0) -> SourceDoc:
    fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
    notes: list[str] = []
    api = _ecfr_api_url(url)
    target = api or url
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml,application/pdf;q=0.9,*/*;q=0.8",
    }
    with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
        resp = client.get(target)
    ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    raw = resp.content
    final_url = str(resp.url)
    sha = hashlib.sha256(raw).hexdigest()
    if resp.status_code != 200:
        notes.append(f"HTTP {resp.status_code}")
        return SourceDoc(source_id, url, final_url, ctype, raw, "", "", fetched_at, sha, "none", notes)
    if final_url.rstrip("/") != url.rstrip("/"):
        notes.append(f"redirected to {final_url}")

    if ctype == "application/pdf" or raw[:5] == b"%PDF-":
        import pymupdf

        doc = pymupdf.open(stream=raw, filetype="pdf")
        text = "\n".join(page.get_text() for page in doc)
        title = (doc.metadata or {}).get("title") or text.strip().split("\n")[0][:120]
        notes.append(f"pdf {len(doc)} pages")
        return SourceDoc(
            source_id, url, final_url, "application/pdf", raw, text, title, fetched_at, sha, "pymupdf", notes
        )

    body = raw.decode("utf-8", errors="ignore")
    if api:
        title, text = _ecfr_xml_text(body)
        return SourceDoc(
            source_id, url, final_url, "application/xml", raw, text, title, fetched_at, sha, "ecfr-api", notes
        )

    nd = _next_data_text(body)
    if nd and len(nd[1]) > 200:
        title, text = nd
        m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
        title = title or (unescape(m.group(1)).strip() if m else "")
        return SourceDoc(source_id, url, final_url, ctype, raw, text, title, fetched_at, sha, "next-data", notes)

    import trafilatura

    text = trafilatura.extract(body, include_tables=True, include_links=False, favor_recall=True) or ""
    m = re.search(r"<title[^>]*>(.*?)</title>", body, re.S)
    title = unescape(m.group(1)).strip() if m else ""
    if len(text) < 200:
        notes.append("little or no server-rendered text (JS-rendered page?)")
    return SourceDoc(source_id, url, final_url, ctype, raw, text, title, fetched_at, sha, "trafilatura", notes)


EXT = {"application/pdf": "pdf", "application/xml": "xml", "text/html": "html"}


def archive(doc: SourceDoc, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = EXT.get(doc.content_type, "bin")
    raw_path = out_dir / f"{doc.source_id}.{ext}"
    txt_path = out_dir / f"{doc.source_id}.txt"
    meta_path = out_dir / f"{doc.source_id}.meta.json"
    raw_path.write_bytes(doc.raw)
    txt_path.write_text(doc.text)
    meta = {
        "source_id": doc.source_id,
        "url": doc.url,
        "final_url": doc.final_url,
        "content_type": doc.content_type,
        "title": doc.title,
        "fetched_at": doc.fetched_at,
        "sha256": doc.sha256,
        "extractor": doc.extractor,
        "chars": len(doc.text),
        "ok": doc.ok,
        "notes": doc.notes,
        "raw_path": str(raw_path.name),
        "text_path": str(txt_path.name),
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    return meta


def load_archived(source_id: str, out_dir: Path) -> tuple[dict, str] | None:
    meta_path = out_dir / f"{source_id}.meta.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text())
    text = (out_dir / meta["text_path"]).read_text()
    return meta, text


def fetch_all(sources: list[dict], out_dir: Path, rebuild: bool = False) -> list[dict]:
    """sources: [{id, url, tier, title?, notes?}] from samples/<client>/sources.json"""
    results = []
    for s in sources:
        cached = None if rebuild else load_archived(s["id"], out_dir)
        if cached:
            meta, _ = cached
            meta["cached"] = True
            results.append(meta)
            continue
        log.info("fetching %s %s", s["id"], s["url"])
        try:
            doc = fetch_source(s["id"], s["url"])
        except Exception as e:  # noqa: BLE001
            log.warning("fetch failed for %s: %s", s["url"], e)
            results.append({"source_id": s["id"], "url": s["url"], "ok": False, "notes": [f"fetch error: {e}"]})
            continue
        meta = archive(doc, out_dir)
        meta["cached"] = False
        results.append(meta)
    return results
