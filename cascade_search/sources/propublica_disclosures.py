"""ProPublica — Trump Team Financial Disclosures.

Reverse-engineered 2026-08-19. The public page is a SvelteKit app, so the HTML
contains no results -- but SvelteKit exposes its loader data at __data.json:

    /trump-team-financial-disclosures/search/__data.json?q=<query>

IMPORTANT: the parameter is `q`, NOT `search`. Using `search=` silently returns
the UNFILTERED index (1,607 rows) with q='' -- i.e. it looks like a successful
broad result rather than an error. That is exactly the kind of silent-wrong
answer this tool exists to prevent, so we verify the echoed `q` matches what we
asked for and downgrade to a blocker if it does not.

SvelteKit flattens its payload into an index-referenced array: values may be
integers pointing at other slots. deref() walks that.

Covers ~1,600 appointees. Searching an asset name (e.g. "Blue Owl") returns
every appointee disclosing it, with agency aggregations.
"""
from __future__ import annotations

import json
import re
import time
from urllib.parse import quote

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, RateLimited,
                            Result, verified_absence)
from ..core.store import Store, cache_key

BASE = "https://projects.propublica.org/trump-team-financial-disclosures"
SOURCE = "propublica_disclosures"


def _deref(arr: list, value, depth: int = 0):
    """Resolve SvelteKit's index-referenced payload into plain data."""
    if depth > 10:
        return None
    if isinstance(value, int) and 0 <= value < len(arr):
        return _deref(arr, arr[value], depth + 1)
    if isinstance(value, dict):
        return {k: _deref(arr, v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_deref(arr, v, depth + 1) for v in value]
    return value


def search(query: str, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search disclosures by asset, entity, or appointee name."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    key = cache_key(SOURCE, query)
    if use_cache:
        cached = store.get(key)
        if cached is not None:
            cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"], cache_hits=1)
            return Hit(query=query, coverage=cov, results=[Result(**r) for r in cached]) \
                if cached else verified_absence(query, cov, "propublica-trump-disclosures")

    allowed, retry, why = limiter.check(SOURCE)
    if not allowed:
        return RateLimited(query=query, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    url = f"{BASE}/search/__data.json?q={quote(query)}"
    t0 = time.time()
    body, blocked = fetch(url, source=SOURCE, query=query)
    limiter.record(SOURCE)
    if blocked:
        blocked.coverage = Coverage(queried=[SOURCE], errored={SOURCE: "blocked"})
        return blocked

    try:
        data = json.loads(body)
        arr = [n for n in data["nodes"] if isinstance(n, dict) and n.get("type") == "data"][0]["data"]
        top = arr[0]
        echoed = _deref(arr, top.get("q"))
        rows = _deref(arr, top.get("result")) or []
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        return AccessBlocker(query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
                             mechanism=Blocker.SERVER_ERROR, url=url,
                             detail=f"payload shape changed ({e}) -- ProPublica may have altered the app")

    # Guard: a wrong param name returns the UNFILTERED index with q=''. Never
    # let that masquerade as a broad hit.
    if (echoed or "").strip().lower() != query.strip().lower():
        return AccessBlocker(
            query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "query-not-applied"}),
            mechanism=Blocker.SERVER_ERROR, url=url,
            detail=(f"server echoed q={echoed!r} for query {query!r}: the filter was NOT applied. "
                    "Results would be the unfiltered index. Refusing to return them."))

    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"],
                   elapsed_ms=int((time.time() - t0) * 1000))
    if not rows:
        if use_cache:
            store.put(key, SOURCE, [], ttl_s=86400)
        return verified_absence(query, cov,
                                "propublica trump-team financial disclosures (~1,600 appointees)")

    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        # Field names verified against a live payload 2026-08-19.
        name = r.get("a_txt") or ""
        slug = r.get("a_slug") or ""
        agency = r.get("agency_name") or ""
        title = r.get("title") or ""
        nw = r.get("net_worth_low")

        # `highlights` carries the matched asset text with <mark> tags -- this is
        # WHY the appointee matched, and it is the most useful field for a
        # reporter. Strip the markup, keep the matched strings.
        matched: list[str] = []
        raw_hl = r.get("highlights")
        if raw_hl:
            try:
                hl = json.loads(raw_hl) if isinstance(raw_hl, str) else raw_hl
                for v in (hl or {}).values():
                    if v:
                        matched.append(re.sub(r"</?mark>", "", str(v)))
            except (json.JSONDecodeError, AttributeError):
                pass

        bits = [b for b in (agency, title) if b]
        if nw:
            try:
                bits.append(f"net worth from ${int(nw):,}")
            except (TypeError, ValueError):
                bits.append(f"net worth from {nw}")
        if matched:
            bits.append(f"matched: {matched[0][:120]}")

        out.append(Result(
            url=f"{BASE}/appointee/{slug}" if slug else f"{BASE}/?search={quote(query)}",
            title=name or slug or "(unnamed appointee)",
            snippet=" | ".join(bits),
            source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
            meta={"appointee": name, "slug": slug, "agency": agency, "title": title,
                  "net_worth_low": nw, "matched_assets": matched,
                  "earliest_document": r.get("document_received_date_earliest")},
        ))
    if use_cache:
        store.put(key, SOURCE, [x.__dict__ for x in out], ttl_s=86400)
    return Hit(query=query, coverage=cov, results=out)
