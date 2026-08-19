"""Documentation search — the gap cascade-search had.

On 2026-08-19 this tool was asked a pricing question and returned product
announcements, because Google News RSS indexes news, not docs. The answer came
from fetching documentation URLs directly. That is a real capability gap: this
package covers courts, contracts, and disclosures, and had nothing for
"what does the vendor's own documentation say?"

Strategy: many documentation sites publish an llms.txt index (or a sitemap).
Fetch the index, match the query against page titles/paths, then fetch the best
pages. No search engine, no API key, no budget.

Registered sites are those this pipeline actually consults. Add more freely --
the only requirement is an index URL and a base.
"""
from __future__ import annotations

import re
import time

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import Coverage, Hit, RateLimited, Result, verified_absence
from ..core.store import Store, cache_key

SOURCE = "docs"

SITES: dict[str, dict] = {
    "claude-code": {"index": "https://code.claude.com/docs/llms.txt",
                    "base": "https://code.claude.com/docs/"},
    "claude-api":  {"index": "https://platform.claude.com/docs/llms.txt",
                    "base": "https://platform.claude.com/docs/"},
}

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def _score(query: str, title: str, url: str) -> int:
    """Cheap lexical scoring. Title matches beat URL matches."""
    terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
    if not terms:
        return 0
    t, u = title.lower(), url.lower()
    return sum((3 if term in t else 0) + (1 if term in u else 0) for term in terms)


def search(query: str, site: str = "claude-code", fetch_top: int = 0,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search a documentation site's index. fetch_top>0 also pulls page text."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    if site not in SITES:
        return verified_absence(query, Coverage(queried=[SOURCE], responsive=[SOURCE]),
                                f"unknown docs site {site!r}; known: {', '.join(SITES)}")

    key = cache_key(SOURCE, query, site=site)
    if use_cache:
        cached = store.get(key)
        if cached is not None:
            cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"], cache_hits=1)
            return Hit(query=query, coverage=cov, results=[Result(**r) for r in cached]) \
                if cached else verified_absence(query, cov, f"docs:{site}")

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=query, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    t0 = time.time()
    body, blocked = fetch(SITES[site]["index"], source=SOURCE, query=query)
    if blocked:
        blocked.coverage = Coverage(queried=[SOURCE], errored={SOURCE: "blocked"})
        return blocked

    scored = []
    for title, url in _LINK.findall(body):
        s = _score(query, title, url)
        if s:
            scored.append((s, title.strip(), url.strip()))
    scored.sort(key=lambda x: -x[0])

    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"],
                   elapsed_ms=int((time.time() - t0) * 1000))
    if not scored:
        if use_cache:
            store.put(key, SOURCE, [], ttl_s=86400)
        return verified_absence(query, cov, f"{site} documentation index ({SITES[site]['index']})")

    out = []
    for rank, (s, title, url) in enumerate(scored[:25], 1):
        r = Result(url=url, title=title, snippet=f"relevance {s}", source=f"{SOURCE}:{site}",
                   engines=[SOURCE], index_origin=["n/a"], score=float(s),
                   meta={"site": site, "rank": rank})
        # Optionally pull the page body so the caller can grep it.
        # Each body fetch is a real call and must claim its own slot; the prior
        # version computed a check and discarded it, then fetched regardless.
        if rank <= fetch_top:
            page_allowed, _, page_why = limiter.reserve(SOURCE)
            if not page_allowed:
                r.meta["text_skipped"] = f"rate limit: {page_why}"
            else:
                page, pblocked = fetch(url, source=SOURCE, query=query)
                if page and not pblocked:
                    text = re.sub(r"\n{3,}", "\n\n", page)
                    r.meta["text"] = text[:20000]
        out.append(r)

    if use_cache:
        store.put(key, SOURCE, [x.__dict__ for x in out], ttl_s=86400)
    return Hit(query=query, coverage=cov, results=out)
