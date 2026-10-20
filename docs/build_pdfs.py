"""Render the two architecture documents to PDF with Chrome headless.

    uv run --with markdown python docs/build_pdfs.py

Outputs land in the repo root: Umpyr-Resolve-Technical-Architecture.pdf, Umpyr-Resolve-Business-Logic.pdf.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parents[1]
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DOCS = ["Umpyr-Resolve-Technical-Architecture", "Umpyr-Resolve-Business-Logic"]

CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1a1a1a; }
h1 { font-size: 22pt; margin: 0 0 4pt; border-bottom: 2px solid #1f4e79; padding-bottom: 6pt; color: #1f4e79; }
h2 { font-size: 14.5pt; margin: 18pt 0 6pt; color: #1f4e79; page-break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; page-break-after: avoid; }
p, li { margin: 3pt 0; }
em { color: #555; }
code { font-family: Menlo, Consolas, monospace; font-size: 9pt; background: #f3f4f6; padding: 0 2px; }
pre { font-family: Menlo, Consolas, monospace; font-size: 8.5pt; background: #f3f4f6; padding: 8px; border-radius: 4px; white-space: pre-wrap; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 9.5pt; page-break-inside: avoid; }
th, td { border: 1px solid #c9ced6; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #eef2f7; }
blockquote { border-left: 3px solid #c9ced6; margin: 6pt 0; padding: 2pt 10pt; color: #444; }
"""


def build(name: str) -> Path:
    src = ROOT / "docs" / f"{name}.md"
    html_body = markdown.markdown(src.read_text(), extensions=["tables", "fenced_code", "sane_lists"])
    html = f"<!doctype html><html><head><meta charset='utf-8'><title>{name}</title><style>{CSS}</style></head><body>{html_body}</body></html>"
    html_path = ROOT / "docs" / f".{name}.html"
    html_path.write_text(html)
    out = ROOT / f"{name}.pdf"
    subprocess.run(
        [CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--print-to-pdf={out}", html_path.as_uri()],
        check=True,
        capture_output=True,
        timeout=120,
    )
    html_path.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    for n in DOCS:
        p = build(n)
        print(p, f"{p.stat().st_size / 1024:.0f} KB")
    sys.exit(0)
