"""Federal Register — rules, notices, and proclamations, with a real count.

The daily journal of federal agency action: proposed and final rules, notices,
executive orders, presidential proclamations. On a capture beat this is where a
policy change becomes citable text with a date and a document number.

Unlike Crossref, its counts ARE measurements. `count` reflects documents matching
the query across the corpus, and the search is scoped to a defined body of
government documents rather than matching loosely across 150M records. "How many
Federal Register documents mention X, and when did they cluster" is a question
this can actually answer.

No key, no advertised limit.
"""
from __future__ import annotations

import json
import time
from urllib.parse import urlencode

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, RateLimited,
                            Result, replay_cached, verified_absence)
from ..core.store import Store, cache_key

SOURCE = "federal_register"
API = "https://www.federalregister.gov/api/v1"

FIELDS = ["document_number", "title", "type", "abstract", "publication_date",
          "html_url", "pdf_url", "agencies", "excerpts", "action", "docket_ids",
          "regulation_id_numbers", "effective_on", "comments_close_on",
          "president", "executive_order_number"]

#: type= filters the API accepts.
DOC_TYPES = {"rule": "RULE", "proposed": "PRORULE", "notice": "NOTICE",
             "presidential": "PRESDOCU"}


def search(query: str, doc_type: str | None = None, agency: str | None = None,
           date_from: str | None = None, date_to: str | None = None,
           per_page: int = 20, page: int = 1, store: Store | None = None,
           limiter: Limiter | None = None, use_cache: bool = True):
    """Search Federal Register documents. `count` is a real total."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    key = cache_key(SOURCE, query, doc_type=doc_type, agency=agency,
                    date_from=date_from, date_to=date_to, page=page, per_page=per_page)
    if use_cache:
        entry = store.get_entry(key)
        if entry is not None:
            return replay_cached(query, entry[0], entry[1], source=SOURCE,
                                 searched=f"federal register ({query})")

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=query, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    params = [("conditions[term]", query), ("per_page", str(per_page)),
              ("page", str(page)), ("order", "newest")]
    params += [("fields[]", f) for f in FIELDS]
    if doc_type:
        t = DOC_TYPES.get(doc_type.lower(), doc_type.upper())
        params.append(("conditions[type][]", t))
    if agency:
        params.append(("conditions[agencies][]", agency))
    if date_from:
        params.append(("conditions[publication_date][gte]", date_from))
    if date_to:
        params.append(("conditions[publication_date][lte]", date_to))

    t0 = time.time()
    url = f"{API}/documents.json?{urlencode(params)}"
    body, blocked = fetch(url, source=SOURCE, query=query)
    limiter.note_outcome(SOURCE, blocked)
    if blocked:
        # The API 404s a search with zero results rather than returning an empty
        # list. That is an ABSENCE, not a wall, and conflating them would be
        # exactly the error this package exists to prevent.
        if getattr(blocked, "mechanism", None) is Blocker.NOT_FOUND:
            return verified_absence(
                query, Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"]),
                f"federal register: no documents matching {query!r}")
        blocked.coverage = Coverage(queried=[SOURCE], errored={SOURCE: "blocked"})
        return blocked

    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return AccessBlocker(query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
                             mechanism=Blocker.SERVER_ERROR, url=url, detail=str(e))

    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"],
                   elapsed_ms=int((time.time() - t0) * 1000))
    total = data.get("count") or 0
    rows = data.get("results") or []
    if not rows:
        return verified_absence(query, cov, f"federal register search for {query!r}")

    out = []
    for r in rows:
        agencies = [a.get("name") for a in (r.get("agencies") or []) if a.get("name")]
        docnum = r.get("document_number") or ""
        rid = f"{SOURCE}:{docnum}"
        try:
            store.put_record(rid, SOURCE, query, r)
        except Exception:
            pass
        out.append(Result(
            url=r.get("html_url") or "",
            title=(r.get("title") or "")[:300],
            snippet=" | ".join(x for x in (
                r.get("type"), r.get("publication_date"),
                ", ".join(agencies[:2])) if x),
            source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
            meta={"record_id": rid, "document_number": docnum,
                  "type": r.get("type"), "agencies": agencies,
                  "publication_date": r.get("publication_date"),
                  "effective_on": r.get("effective_on"),
                  "comments_close_on": r.get("comments_close_on"),
                  "docket_ids": r.get("docket_ids"),
                  "regulation_id_numbers": r.get("regulation_id_numbers"),
                  "executive_order_number": r.get("executive_order_number"),
                  "president": (r.get("president") or {}).get("name")
                               if isinstance(r.get("president"), dict) else r.get("president"),
                  "pdf_url": r.get("pdf_url"), "abstract": r.get("abstract"),
                  "total_matches": total, "page": page,
                  "returned_this_page": len(rows)},
        ))
    if use_cache:
        store.put(key, SOURCE, [x.__dict__ for x in out], ttl_s=86400)
    return Hit(query=query, coverage=cov, results=out)
