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
import time
from urllib.parse import quote

from ..core.archive import archive
from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import Coverage, Hit, RateLimited, Result, replay_cached, verified_absence
from ..core.store import Store, cache_key

API = "https://www.courtlistener.com/api/rest/v4"


def _headers():
    tok = os.environ.get("COURTLISTENER_TOKEN")
    return {"Authorization": f"Token {tok}"} if tok else {}


def search(query: str, kind: str = "r", court: str | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search. kind: r=RECAP dockets, rd=documents, o=opinions, p=people."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    key = cache_key("courtlistener", query, kind=kind, court=court)
    if use_cache:
        entry = store.get_entry(key)
        if entry is not None:
            return replay_cached(query, entry[0], entry[1], source="courtlistener",
                                 searched=f"courtlistener search (type={kind})")

    allowed, retry, why = limiter.reserve("courtlistener")
    if not allowed:
        return RateLimited(query=query,
                           coverage=Coverage(queried=["courtlistener"], rate_limited=["courtlistener"]),
                           source="courtlistener", retry_after_s=int(retry) if retry else None,
                           detail=why + ("  [set COURTLISTENER_TOKEN for higher limits]"
                                         if not os.environ.get("COURTLISTENER_TOKEN") else ""))

    url = f"{API}/search/?q={quote(query)}&type={kind}"
    if court:
        url += f"&court={court}"

    t0 = time.time()
    body, blocked = fetch(url, source="courtlistener", query=query, headers=_headers())
    if blocked:
        blocked.coverage = Coverage(queried=["courtlistener"], errored={"courtlistener": "blocked"})
        return blocked

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return RateLimited(query=query, coverage=Coverage(queried=["courtlistener"],
                                                          errored={"courtlistener": "non-json"}),
                           source="courtlistener", detail="non-JSON response (auth wall or throttle)")

    cov = Coverage(queried=["courtlistener"], responsive=["courtlistener"], indexes=["n/a"],
                   elapsed_ms=int((time.time() - t0) * 1000))
    rows = data.get("results", [])
    if not rows:
        if use_cache:
            store.put(key, "courtlistener", [], ttl_s=86400)
        return verified_absence(query, cov, f"courtlistener search (type={kind})")

    out = []
    for r in rows:
        path = r.get("absolute_url") or ""
        out.append(Result(
            url=f"https://www.courtlistener.com{path}" if path.startswith("/") else path,
            title=r.get("caseName", ""), source="courtlistener", engines=["courtlistener"],
            snippet=f"{r.get('court','')} | {r.get('docketNumber','')} | {r.get('dateFiled','')}",
            meta={k: r.get(k) for k in
                  ("docketNumber", "court", "dateFiled", "judge", "status", "docket_id")},
        ))
    if use_cache:
        store.put(key, "courtlistener", [x.__dict__ for x in out], ttl_s=86400)
    return Hit(query=query, coverage=cov, results=out)


def document(storage_url: str, filename: str, store: Store | None = None,
             limiter: Limiter | None = None):
    """Fetch a RECAP PDF from storage.courtlistener.com and archive it.

    Use the storage host: courtlistener.com/recap 403s to automated clients.
    """
    store = store or Store()
    limiter = limiter or Limiter(store)
    allowed, retry, why = limiter.reserve("courtlistener")
    if not allowed:
        return RateLimited(query=filename,
                           coverage=Coverage(queried=["courtlistener"], rate_limited=["courtlistener"]),
                           source="courtlistener", retry_after_s=int(retry) if retry else None, detail=why)

    data, blocked = fetch(storage_url, source="courtlistener", query=filename, binary=True)
    if blocked:
        return blocked
    info = archive(data, filename, storage_url, "cascade-search:courtlistener")
    return Hit(query=filename,
               coverage=Coverage(queried=["courtlistener"], responsive=["courtlistener"]),
               results=[Result(url=storage_url, title=filename, source="courtlistener",
                               engines=["courtlistener"], meta={"archived": info})])
