"""CourtListener — federal and state court records.

Rate limits (wiki.free.law, v4 overview, fetched 2026-08-19): 5/min, 50/hr,
125/day authenticated; windows apply CONCURRENTLY and "the most restrictive one
-- given your recent traffic -- is what controls". Unauthenticated is stricter.

Two access facts learned the hard way on 2026-08-19:
  * courtlistener.com/recap 403s to automated fetch, but the SAME PDFs are
    served from storage.courtlistener.com.
  * The dockets/docket-entries REST endpoints are auth-walled, while the search
    API with type=r / type=rd plus cursor pagination is open.
"""
from __future__ import annotations

import json
import os
from urllib.parse import quote

from ..core.archive import archive
from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import Coverage, Hit, RateLimited, Result
from ..core.source import run_source
from ..core.store import Store

API = "https://www.courtlistener.com/api/rest/v4"
SOURCE = "courtlistener"


def _headers():
    tok = os.environ.get("COURTLISTENER_TOKEN")
    return {"Authorization": f"Token {tok}"} if tok else {}


def _parse(body: str) -> list[Result]:
    """Parse a search response.

    CourtListener reports the TOTAL match count alongside the (paged) rows.
    Discarding it silently turned every "count the X" question into a floor:
    a live worker counting an industry could only write ">=20 dockets" when the
    API had plainly said 155. A floor reported as a finding understates by
    whatever the page cap happens to be, which is not a small error on a beat
    where scale IS the claim. Carry it on every Result so the caller can say
    what is actually true.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"non-JSON response (auth wall or throttle): {e}") from e

    total = data.get("count")
    returned = len(data.get("results", []))
    out = []
    for r in data.get("results", []):
        path = r.get("absolute_url") or ""
        out.append(Result(
            url=f"https://www.courtlistener.com{path}" if path.startswith("/") else path,
            title=r.get("caseName", ""), source=SOURCE, engines=[SOURCE],
            snippet=f"{r.get('court','')} | {r.get('docketNumber','')} | {r.get('dateFiled','')}",
            meta={**{k: r.get(k) for k in
                     ("docketNumber", "court", "dateFiled", "judge", "status", "docket_id")},
                  "total_matches": total, "returned_this_page": returned,
                  "more_available": bool(data.get("next"))},
        ))
    return out


def search(query: str, kind: str = "r", court: str | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search. kind: r=RECAP dockets, rd=documents, o=opinions, p=people."""
    url = f"{API}/search/?q={quote(query)}&type={kind}"
    if court:
        url += f"&court={court}"
    return run_source(
        query, source=SOURCE, url=url,
        searched=f"courtlistener search (type={kind})",
        parse=_parse, store=store, limiter=limiter, use_cache=use_cache,
        cache_params={"kind": kind, "court": court}, headers=_headers(),
    )


def document(storage_url: str, filename: str, store: Store | None = None,
             limiter: Limiter | None = None):
    """Fetch a RECAP PDF from storage.courtlistener.com and archive it.

    Use the storage host: courtlistener.com/recap 403s to automated clients.
    """
    store = store or Store()
    limiter = limiter or Limiter(store)
    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=filename,
                           coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    data, blocked = fetch(storage_url, source=SOURCE, query=filename, binary=True)
    if blocked:
        return blocked
    info = archive(data, filename, storage_url, "cascade-search:courtlistener")
    return Hit(query=filename,
               coverage=Coverage(queried=[SOURCE], responsive=[SOURCE]),
               results=[Result(url=storage_url, title=filename, source="courtlistener",
                               engines=["courtlistener"], meta={"archived": info})])
