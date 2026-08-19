"""USAspending — federal awards, with a REAL total.

Two workers independently called this "the load-bearing source here" on a
procurement beat, and its absence forced both into raw HTTP. It closes a gap the
other sources cannot: `spending_by_award_count` returns an actual count by award
type, so "how many federal awards does this vendor hold" is answerable rather
than estimable. Contrast `web`, whose engines ignore quoted phrases and whose
totals are not countable at all.

The POST-body API takes no key and advertises no rate limit; we pace
conservatively anyway, because an unmetered public API is a courtesy, not a
license.

TWO WAYS TO SEARCH, and the difference decides whether your number means
anything:

  --recipient   `recipient_search_text` -- matches the RECIPIENT NAME. Use this
                when you want a vendor's awards. Precise.
  --keywords    `keywords` -- full-text across award descriptions and more. Use
                for discovery. It is FUZZY: searching "Force Science" returns
                NURAD Technologies, because the words appear somewhere in the
                record. A count from a keyword search is not a count OF anything
                in particular, so the client labels it.
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, RateLimited,
                            Result, replay_cached, verified_absence)
from ..core.store import Store, cache_key

SOURCE = "usaspending"
API = "https://api.usaspending.gov/api/v2"

#: Contracts, IDVs, grants, direct payments, loans, other.
ALL_AWARD_TYPES = ["A", "B", "C", "D", "IDV_A", "IDV_B", "IDV_C", "IDV_D", "IDV_E",
                   "02", "03", "04", "05", "06", "07", "08", "09", "10", "11"]
CONTRACT_TYPES = ["A", "B", "C", "D"]

#: `internal_id` is NOT requestable -- asking for it 500s the endpoint -- but the
#: API returns it anyway, and it is what builds the award's usaspending.gov URL.
FIELDS = ["Award ID", "Recipient Name", "Awarding Agency", "Awarding Sub Agency",
          "Award Amount", "Total Outlays", "Start Date", "End Date",
          "Description", "Contract Award Type", "recipient_id"]


def _post(path: str, body: dict, timeout: float = 45.0):
    """POST JSON. `fetch` is GET-only, so this is deliberate and local."""
    import httpx
    try:
        r = httpx.post(f"{API}{path}", json=body, timeout=timeout,
                       headers={"User-Agent": "cascade-search",
                                "Content-Type": "application/json"})
    except httpx.TimeoutException as e:
        return None, ("timeout", str(e))
    except httpx.HTTPError as e:
        return None, ("http", str(e))
    if r.status_code == 429:
        return None, ("rate", r.headers.get("Retry-After", "60"))
    if r.status_code >= 400:
        return None, ("status", f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json(), None
    except json.JSONDecodeError as e:
        return None, ("parse", str(e))


def _filters(query: str, by_recipient: bool, award_types: list[str],
             date_from: str | None, date_to: str | None) -> dict:
    f: dict[str, Any] = {"award_type_codes": award_types}
    if by_recipient:
        f["recipient_search_text"] = [query]
    else:
        f["keywords"] = [query]
    if date_from or date_to:
        f["time_period"] = [{"start_date": date_from or "2007-10-01",
                             "end_date": date_to or "2030-09-30"}]
    return f


def counts(query: str, by_recipient: bool = True, award_types: list[str] | None = None,
           date_from: str | None = None, date_to: str | None = None,
           store: Store | None = None, limiter: Limiter | None = None):
    """Real totals by award type. The countable primitive this beat needs."""
    store = store or Store()
    limiter = limiter or Limiter(store)
    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=query, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    data, err = _post("/search/spending_by_award_count/",
                      {"filters": _filters(query, by_recipient,
                                           award_types or ALL_AWARD_TYPES,
                                           date_from, date_to)})
    if err:
        kind, detail = err
        if kind in ("timeout", "rate"):
            return RateLimited(query=query,
                               coverage=Coverage(queried=[SOURCE], errored={SOURCE: kind}),
                               source=SOURCE, retry_after_s=60, detail=detail)
        return AccessBlocker(query=query,
                             coverage=Coverage(queried=[SOURCE], errored={SOURCE: kind}),
                             mechanism=Blocker.SERVER_ERROR, url=f"{API}/search/spending_by_award_count/",
                             detail=detail)

    res = data.get("results") or {}
    total = sum(v for v in res.values() if isinstance(v, int))
    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"])
    mode = "recipient-name" if by_recipient else "keyword (FUZZY)"
    if not total:
        return verified_absence(query, cov, f"usaspending award counts ({mode})")
    return Hit(query=query, coverage=cov, results=[Result(
        url=f"https://www.usaspending.gov/search?keywords={query}",
        title=f"{total} federal awards matching {query!r}",
        snippet=" | ".join(f"{k}: {v}" for k, v in res.items() if v),
        source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
        meta={"total_awards": total, "by_type": res, "search_mode": mode,
              "count_caveat": (None if by_recipient else
                               "KEYWORD search is full-text across descriptions: this "
                               "counts records CONTAINING the words, not awards TO a "
                               "vendor. Use --recipient for a vendor total.")},
    )])


def search(query: str, by_recipient: bool = True, award_types: list[str] | None = None,
           date_from: str | None = None, date_to: str | None = None, limit: int = 25,
           page: int = 1, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """List awards. Carries the real total from the count endpoint alongside."""
    store = store or Store()
    limiter = limiter or Limiter(store)
    types = award_types or CONTRACT_TYPES

    key = cache_key(SOURCE, query, recipient=by_recipient, types=",".join(types),
                    date_from=date_from, date_to=date_to, page=page, limit=limit)
    if use_cache:
        entry = store.get_entry(key)
        if entry is not None:
            return replay_cached(query, entry[0], entry[1], source=SOURCE,
                                 searched=f"usaspending awards ({query})")

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=query, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    t0 = time.time()
    data, err = _post("/search/spending_by_award/", {
        "filters": _filters(query, by_recipient, types, date_from, date_to),
        "fields": FIELDS, "limit": limit, "page": page,
        "sort": "Award Amount", "order": "desc"})
    if err:
        kind, detail = err
        if kind in ("timeout", "rate"):
            return RateLimited(query=query,
                               coverage=Coverage(queried=[SOURCE], errored={SOURCE: kind}),
                               source=SOURCE, retry_after_s=60, detail=detail)
        return AccessBlocker(query=query,
                             coverage=Coverage(queried=[SOURCE], errored={SOURCE: kind}),
                             mechanism=Blocker.SERVER_ERROR,
                             url=f"{API}/search/spending_by_award/", detail=detail)

    rows = data.get("results") or []
    pm = data.get("page_metadata") or {}
    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"],
                   elapsed_ms=int((time.time() - t0) * 1000))
    if not rows:
        if use_cache:
            store.put(key, SOURCE, [], ttl_s=86400)
        return verified_absence(query, cov,
                                f"usaspending awards, {'recipient' if by_recipient else 'keyword'} "
                                f"search across {len(types)} award types")

    out = []
    for r in rows:
        aid = r.get("Award ID") or ""
        internal = r.get("internal_id")
        # Full record persisted; result carries the fields that answer questions.
        rid = f"{SOURCE}:{internal or aid}"
        try:
            store.put_record(rid, SOURCE, query, r)
        except Exception:
            pass
        out.append(Result(
            url=(f"https://www.usaspending.gov/award/{internal}" if internal
                 else f"https://www.usaspending.gov/search?keywords={aid}"),
            title=f"{r.get('Recipient Name','?')} — {aid}",
            snippet=" | ".join(x for x in (
                f"${r.get('Award Amount'):,.0f}" if isinstance(r.get("Award Amount"), (int, float)) else None,
                r.get("Awarding Agency"), r.get("Contract Award Type"),
                f"{r.get('Start Date','')}→{r.get('End Date','')}") if x),
            source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
            meta={"record_id": rid, "award_id": aid,
                  "recipient": r.get("Recipient Name"),
                  "amount": r.get("Award Amount"), "outlays": r.get("Total Outlays"),
                  "agency": r.get("Awarding Agency"), "sub_agency": r.get("Awarding Sub Agency"),
                  "award_type": r.get("Contract Award Type"),
                  "start": r.get("Start Date"), "end": r.get("End Date"),
                  "description": r.get("Description"),
                  "page": page, "has_next_page": bool(pm.get("hasNext")),
                  "page_note": ("USAspending paginates without a total; call "
                                "`usaspending --count` for the real figure.")},
        ))
    if use_cache:
        store.put(key, SOURCE, [x.__dict__ for x in out], ttl_s=86400)
    return Hit(query=query, coverage=cov, results=out)
