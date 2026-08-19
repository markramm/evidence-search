"""Local dynamic filtering: return the fields, not the page.

The Claude API's `web_search_20260209+` dynamic filtering runs code that filters
search results BEFORE they enter the context window. That is an API-level tool
parameter and is not exposed as a Claude Code setting -- so we cannot switch it
on for workers. But we can do the same job locally, and for the sources this
pipeline actually uses we can do it better, because we know the schemas.

Measured baseline (2026-08-19 session): 13 workers, 2,317,424 tokens, 1,168 tool
calls -- ~1,984 tokens per tool call. The heaviest workers were not
reasoning-heavy; they were fetch-and-scan loops. Preservation spent 269K across
140 calls; ownership 218K across 126. A worker fetched a page, most of it landed
in context, and it extracted three fields.

This module extracts the fields first. Same information, a fraction of the
tokens, and -- because extraction is deterministic -- no loss of fidelity on the
parts that matter.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

# Cheap, conservative estimate. Good enough to report savings honestly.
CHARS_PER_TOKEN = 4


def est_tokens(s: str) -> int:
    return max(1, len(s) // CHARS_PER_TOKEN)


def page_text(html: str, max_chars: int = 0) -> str:
    """Strip chrome and return readable text.

    Removes script/style/nav/header/footer/aside -- the parts that cost tokens
    and carry no information for an investigator.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript", "svg"]):
        tag.decompose()
    text = soup.get_text("\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return text[:max_chars] if max_chars else text


def grep(html_or_text: str, patterns: list[str], context_chars: int = 160,
         is_html: bool = True, max_hits: int = 40) -> list[dict]:
    """Return only the passages matching the caller's patterns.

    This is the highest-leverage primitive here: a worker asking 'does this page
    mention <person-h> or a manager roster?' should receive four matching
    lines, not a 90KB page.
    """
    text = page_text(html_or_text) if is_html else html_or_text
    out: list[dict] = []
    for pat in patterns:
        try:
            rx = re.compile(pat, re.I)
        except re.error:
            rx = re.compile(re.escape(pat), re.I)
        for m in rx.finditer(text):
            a = max(0, m.start() - context_chars)
            b = min(len(text), m.end() + context_chars)
            out.append({"pattern": pat, "match": m.group(0),
                        "context": re.sub(r"\s+", " ", text[a:b]).strip()})
            if len(out) >= max_hits:
                return out
    return out


# Identifier patterns this beat uses constantly.
# Tightened 2026-08-19 after a first cut matched "BROADCASTING" as a UEI and
# "COURT" as a CAGE code. Government identifiers are alphanumeric MIXTURES; an
# all-letter token is a word. Every pattern below requires at least one digit,
# and the short ones (UEI/CAGE) require both a letter and a digit.
IDENTIFIERS = {
    # e.g. 15DDNE21P00000038, 70CDCR21P00000057, 47QSWA20D002K
    "federal_award":  r"\b\d{2}[A-Z]{2,6}\d{2}[A-Z]\d{5,8}[A-Z]?\b",
    # 1:16-cv-02237, 4:14-cv-00385, CF-2013-00038, CIV-15-188-M
    "docket":         r"\b\d{1,2}:\d{2}-[a-z]{2}-\d{4,6}\b"
                      r"|\bC[FVJMR]-\d{4}-\d{3,5}\b"
                      r"|\bCIV-\d{2}-\d{2,4}-[A-Z]\b",
    # 12 chars, must contain BOTH a letter and a digit (e.g. VG1HDQR1Y1P5)
    "uei":            r"\b(?=[A-Z0-9]{12}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{12}\b",
    # 5 chars, same mixture requirement (e.g. 4ZVJ2)
    "cage":           r"\b(?=[A-Z0-9]{5}\b)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{5}\b",
    "ein":            r"\b\d{2}-\d{7}\b",
    # require thousands or cents -- bare "$1" is noise
    "money":          r"\$\d{1,3}(?:,\d{3})+(?:\.\d{2})?\b|\$\d+\.\d{2}\b",
    "date":           r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/(?:19|20)\d{2}\b",
    "usc_statute":    r"\b\d+\s+U\.?S\.?C\.?\s*§*\s*\d+[a-z]?\b|\b\d+\s+ILCS\s+[\d/.]+\b"
                      r"|\b\d+\s+O\.S\.\s*§*\s*\d+\b",
    "naics_psc":      r"\bNAICS\s*:?\s*\d{6}\b|\bPSC\s*:?\s*[A-Z]\d{3}\b",
}


def identifiers(html_or_text: str, kinds: list[str] | None = None,
                is_html: bool = True) -> dict[str, list[str]]:
    """Pull structured identifiers. Deterministic, and far cheaper than reading."""
    text = page_text(html_or_text) if is_html else html_or_text
    found: dict[str, list[str]] = {}
    for kind in (kinds or IDENTIFIERS):
        rx = IDENTIFIERS.get(kind)
        if not rx:
            continue
        hits = sorted(set(re.findall(rx, text)))
        if hits:
            found[kind] = hits[:60]
    return found


def tables(html: str, min_rows: int = 2) -> list[list[list[str]]]:
    """Extract tables as rows of cells.

    Registry and docket pages carry their payload in tables; a worker needs the
    cells, never the markup.
    """
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for t in soup.find_all("table"):
        rows = []
        for tr in t.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            cells = [c for c in cells if c]
            if cells:
                rows.append(cells)
        if len(rows) >= min_rows:
            out.append(rows)
    return out


def savings(raw: str, extracted: str) -> dict:
    """Report honestly what the filtering saved -- including when it saved nothing.

    A negative "reduction" is not a saving and should never be printed as one.
    It happens legitimately: OCR output plus its provenance block can exceed a
    short scanned page's decoded text. Report that as expansion, plainly, rather
    than as a percentage with a minus sign that reads like a malfunction.
    """
    r, e = est_tokens(raw), est_tokens(extracted)
    pct = round(100 * (1 - e / r), 1) if r else 0.0
    return {"raw_tokens_est": r, "extracted_tokens_est": e,
            "saved_est": r - e,
            "reduction_pct": pct,
            "expanded": e > r}
