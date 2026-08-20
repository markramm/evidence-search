"""Crossref — resolve a paywalled citation into a verifiable record.

A worker hit a wall on this beat: SAGE served Cloudflare on a Police Quarterly
reply it needed to cite, and the full text stayed unread. Crossref does not
return full text either -- but it returns the AUTHORITATIVE bibliographic
record: exact title, journal, all authors, date, and any open-access link the
publisher has registered. That is usually what a citation actually needs.

IMPORTANT -- this is a RESOLVER, not a counting source. Crossref's search totals
are worthless as measurements: `query.bibliographic` for "Forced Science"
reports 9,253,640 results and the top hit is "What is Forced Labour?"; even
`query.title` reports 678,393 with an unrelated first result. It matches loosely
across ~150M records by design.

DOI lookup, by contrast, is exact and authoritative. `works/10.1177/...` returned
precisely the paper the worker wanted, with all four authors. So:

  * Have a DOI     -> `crossref <doi>`. Exact. Trust it.
  * Have a title   -> `crossref "title words"`. Discovery only; VERIFY the match
                      before citing, because a plausible near-miss is the
                      characteristic failure here.

Crossref asks for a mailto in the User-Agent as a courtesy (the "polite pool"),
which we send.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from ..core.limits import Limiter
from ..core.results import Blocker, Coverage, Result, verified_absence
from ..core.source import run_source
from ..core.store import Store

SOURCE = "crossref"
API = "https://api.crossref.org"
MAILTO = "mark.ramm@gmail.com"
UA = f"evidence-search (mailto:{MAILTO})"

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b")


def looks_like_doi(s: str) -> bool:
    return bool(_DOI_RE.fullmatch(s.strip()) or s.strip().lower().startswith("10."))


def _record(m: dict, exact: bool) -> Result:
    title = (m.get("title") or [""])[0]
    journal = (m.get("container-title") or [""])[0]
    authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x)
               for a in (m.get("author") or [])]
    year = ((m.get("issued") or {}).get("date-parts") or [[None]])[0][0]
    doi = m.get("DOI", "")

    # An open-access link, when the publisher registered one -- the difference
    # between a citable record and a readable paper.
    oa = ""
    for lk in (m.get("link") or []):
        if lk.get("content-type") in ("application/pdf", "text/html"):
            oa = lk.get("URL", "")
            break

    return Result(
        url=m.get("URL") or (f"https://doi.org/{doi}" if doi else ""),
        title=title,
        snippet=" | ".join(x for x in (
            journal, str(year) if year else None,
            ", ".join(authors[:4]) + ("  et al." if len(authors) > 4 else "")) if x),
        source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
        meta={"doi": doi, "journal": journal, "year": year, "authors": authors,
              "type": m.get("type"), "publisher": m.get("publisher"),
              "open_access_url": oa,
              "cited_by": m.get("is-referenced-by-count"),
              "exact": exact,
              "verify_note": (None if exact else
                              "TITLE SEARCH is fuzzy -- Crossref matches loosely across "
                              "~150M records. Confirm this is the paper you meant before "
                              "citing it; a plausible near-miss is the failure mode here."),
              "full_text_note": ("Crossref returns metadata, not full text. Use "
                                 "open_access_url if present, else the publisher's site "
                                 "(often walled) or a library."),
              })


def _parse_doi(body: str, doi: str, store) -> list[Result]:
    """One authoritative record. A missing `message` is a shape change.

    run_source turns a ValueError here into a blocked outcome, never an
    absence: a payload that stopped parsing is not evidence the DOI is
    unregistered. That distinction is the whole point of the 404 handler below.
    """
    try:
        m = json.loads(body)["message"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ValueError(f"no `message` in response: {e}") from e

    try:
        store.put_record(f"{SOURCE}:{m.get('DOI')}", SOURCE, doi, m)
    except Exception:
        pass
    return [_record(m, exact=True)]


def _unregistered_doi(doi: str):
    """A 404 on a DOI lookup means the DOI is not registered -- a FINDING, and
    a different one from being blocked. Every other status stays a blocker."""
    def handler(blocked, body):
        if getattr(blocked, "mechanism", None) is Blocker.NOT_FOUND:
            return verified_absence(
                doi, Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"]),
                f"crossref: DOI {doi} is not registered")
        return None
    return handler


def by_doi(doi: str, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Exact lookup. This is the authoritative path."""
    # Resolve the store here rather than letting run_source default it, so the
    # parser has somewhere to persist full records even when the caller passed
    # nothing.
    store = store or Store()
    doi = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")

    return run_source(
        doi, source=SOURCE, url=f"{API}/works/{quote(doi, safe='')}",
        searched=f"crossref DOI {doi}",
        parse=lambda b: _parse_doi(b, doi, store),
        store=store, limiter=limiter, use_cache=use_cache,
        on_blocked=_unregistered_doi(doi),
        headers={"User-Agent": UA},
        ttl_s=604800,                      # bibliographic metadata is stable
        exact_match_supported=True,
        corpus="Crossref DOI registry: the authoritative bibliographic record.",
        cache_params={"mode": "doi"},
    )


def _parse_title(body: str, query: str, store) -> list[Result]:
    try:
        msg = json.loads(body)["message"]
    except (json.JSONDecodeError, KeyError) as e:
        raise ValueError(f"no `message` in response: {e}") from e

    items = msg.get("items") or []
    out = [_record(m, exact=False) for m in items]
    for m in items:
        try:
            store.put_record(f"{SOURCE}:{m.get('DOI')}", SOURCE, query, m)
        except Exception:
            pass
    return out


def search(query: str, rows: int = 10, store: Store | None = None,
           limiter: Limiter | None = None, use_cache: bool = True):
    """Title search. DISCOVERY ONLY -- see the module docstring on why the
    totals are not measurements."""
    store = store or Store()
    if looks_like_doi(query):
        return by_doi(query, store, limiter, use_cache)

    return run_source(
        query, source=SOURCE,
        url=f"{API}/works?query.title={quote(query)}&rows={rows}",
        searched="crossref title search (FUZZY)",
        parse=lambda b: _parse_title(b, query, store),
        store=store, limiter=limiter, use_cache=use_cache,
        headers={"User-Agent": UA},
        # Crossref matches loosely across ~150M records by design, so a zero
        # here is a weak negative and must say so rather than reading like a
        # corpus-backed absence.
        exact_match_supported=False,
        corpus=("Crossref works index (~150M records), matched LOOSELY on "
                "title. Totals are not measurements."),
        caveats=["FUZZY title search: Crossref matches loosely across ~150M "
                 "records. This negative is weak -- a differently-worded title "
                 "would not be found. Prefer a DOI lookup where one exists."],
        not_searched=["works with no Crossref DOI registered",
                      "full text -- Crossref indexes metadata only"],
        cache_params={"mode": "title", "rows": rows},
    )
