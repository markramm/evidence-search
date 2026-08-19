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
import time
from urllib.parse import quote

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, RateLimited,
                            Result, replay_cached, verified_absence)
from ..core.store import Store, cache_key

SOURCE = "crossref"
API = "https://api.crossref.org"
MAILTO = "mark.ramm@gmail.com"
UA = f"cascade-search (mailto:{MAILTO})"

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


def by_doi(doi: str, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Exact lookup. This is the authoritative path."""
    store = store or Store()
    limiter = limiter or Limiter(store)
    doi = doi.strip().removeprefix("https://doi.org/").removeprefix("doi:")

    key = cache_key(SOURCE, doi, mode="doi")
    if use_cache:
        entry = store.get_entry(key)
        if entry is not None:
            return replay_cached(doi, entry[0], entry[1], source=SOURCE,
                                 searched=f"crossref DOI {doi}")

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=doi, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    url = f"{API}/works/{quote(doi, safe='')}"
    body, blocked = fetch(url, source=SOURCE, query=doi, headers={"User-Agent": UA})
    if blocked:
        # A 404 here means the DOI is not registered -- which IS a finding, and a
        # different one from being blocked.
        if getattr(blocked, "mechanism", None) is Blocker.NOT_FOUND:
            return verified_absence(
                doi, Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"]),
                f"crossref: DOI {doi} is not registered")
        blocked.coverage = Coverage(queried=[SOURCE], errored={SOURCE: "blocked"})
        return blocked

    try:
        m = json.loads(body)["message"]
    except (json.JSONDecodeError, KeyError) as e:
        return AccessBlocker(query=doi, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
                             mechanism=Blocker.SERVER_ERROR, url=url, detail=str(e))

    r = _record(m, exact=True)
    try:
        store.put_record(f"{SOURCE}:{m.get('DOI')}", SOURCE, doi, m)
    except Exception:
        pass
    if use_cache:
        store.put(key, SOURCE, [r.__dict__], ttl_s=604800)   # metadata is stable
    return Hit(query=doi, coverage=Coverage(queried=[SOURCE], responsive=[SOURCE],
                                            indexes=["n/a"]), results=[r])


def search(query: str, rows: int = 10, store: Store | None = None,
           limiter: Limiter | None = None, use_cache: bool = True):
    """Title search. DISCOVERY ONLY -- see the module docstring on why the
    totals are not measurements."""
    store = store or Store()
    limiter = limiter or Limiter(store)
    if looks_like_doi(query):
        return by_doi(query, store, limiter, use_cache)

    key = cache_key(SOURCE, query, mode="title", rows=rows)
    if use_cache:
        entry = store.get_entry(key)
        if entry is not None:
            return replay_cached(query, entry[0], entry[1], source=SOURCE,
                                 searched="crossref title search (FUZZY)")

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=query, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    t0 = time.time()
    url = f"{API}/works?query.title={quote(query)}&rows={rows}"
    body, blocked = fetch(url, source=SOURCE, query=query, headers={"User-Agent": UA})
    if blocked:
        blocked.coverage = Coverage(queried=[SOURCE], errored={SOURCE: "blocked"})
        return blocked

    try:
        msg = json.loads(body)["message"]
    except (json.JSONDecodeError, KeyError) as e:
        return AccessBlocker(query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
                             mechanism=Blocker.SERVER_ERROR, url=url, detail=str(e))

    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"],
                   elapsed_ms=int((time.time() - t0) * 1000))
    items = msg.get("items") or []
    if not items:
        return verified_absence(query, cov, "crossref title search")

    out = [_record(m, exact=False) for m in items]
    for m, r in zip(items, out):
        try:
            store.put_record(f"{SOURCE}:{m.get('DOI')}", SOURCE, query, m)
        except Exception:
            pass
    if use_cache:
        store.put(key, SOURCE, [x.__dict__ for x in out], ttl_s=86400)
    return Hit(query=query, coverage=cov, results=out)
