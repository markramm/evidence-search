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


def _parse(body: str, query: str = "", store=None) -> list[Result]:
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
        # RECAP rows carry `docket_absolute_url`; opinion rows carry
        # `absolute_url`. Taking only the latter emitted an empty url for every
        # RECAP hit while `docket_id` -- everything needed to build the link --
        # sat unused in the same response.
        path = r.get("absolute_url") or r.get("docket_absolute_url") or ""
        if not path and r.get("docket_id"):
            path = f"/docket/{r['docket_id']}/"

        # Curated view in the result; FULL record in the store.
        #
        # Returning all 30 fields inline spends the caller's context on data
        # nobody asked for -- the opposite of what `extract` is for. Returning
        # 4 loses the ones that answer the question. So: persist the upstream
        # object verbatim under a stable id, surface the fields that are
        # generally useful on this beat, and let a caller who needs the rest
        # fetch it with `evidence-search record <id>`.
        record_id = f"{SOURCE}:{r.get('docket_id') or r.get('id') or r.get('docketNumber')}"
        if store is not None:
            try:
                store.put_record(record_id, SOURCE, query, r)
            except Exception:
                pass   # a storage failure must never lose the caller's results

        meta = {k: r.get(k) for k in (
            # identity
            "docketNumber", "court", "court_citation_string", "dateFiled",
            "dateTerminated", "docket_id",
            # posture -- who sued whom, over what, before whom
            "cause", "suitNature", "jurisdictionType", "assignedTo", "status",
            # WHO RETAINED THE EXPERT: the money question, and the reason this
            # curation exists at all
            "firm", "attorney", "party",
        ) if r.get(k) not in (None, "", [], {})}
        nxt = data.get("next") or ""
        next_cursor = ""
        if nxt and "cursor=" in nxt:
            from urllib.parse import unquote as _unq
            next_cursor = _unq(nxt.split("cursor=", 1)[1].split("&")[0])
        meta.update({"record_id": record_id, "total_matches": total,
                     "returned_this_page": returned,
                     "more_available": bool(nxt),
                     "next_cursor": next_cursor,
                     "paging_note": ("v4 pages by CURSOR, not page number. Pass "
                                     "meta.next_cursor to --cursor for the next page.")})

        # The heaviest field, summarised: descriptions are what say whether a
        # filing is an expert disclosure. Full text stays in the record.
        docs = r.get("recap_documents") or []
        if docs:
            meta["recap_documents"] = [
                {"description": d.get("description"), "url": d.get("absolute_url")}
                for d in docs[:10]]
            meta["recap_document_count"] = len(docs)

        out.append(Result(
            url=f"https://www.courtlistener.com{path}" if path.startswith("/") else path,
            title=r.get("caseName", ""), source=SOURCE, engines=[SOURCE],
            snippet=f"{r.get('court','')} | {r.get('docketNumber','')} | {r.get('dateFiled','')}",
            meta=meta,
        ))
    return out


def search(query: str, kind: str = "r", court: str | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True, cursor: str | None = None):
    """Search. kind: r=RECAP dockets, rd=documents, o=opinions, p=people."""
    # Resolve the store here rather than letting run_source default it, so the
    # parser has somewhere to persist full records even when the caller passed
    # nothing. Otherwise records vanish exactly for the simplest call sites.
    store = store or Store()
    url = f"{API}/search/?q={quote(query)}&type={kind}"
    if court:
        url += f"&court={court}"
    # v4 paginates by CURSOR, not page number: `&page=2` is silently ignored and
    # returns page 1 again. A --page flag here would have been a lie, so the
    # caller passes the opaque cursor from a prior result's meta.next_cursor.
    if cursor:
        url += f"&cursor={quote(cursor)}"
    return run_source(
        query, source=SOURCE, url=url,
        searched=f"courtlistener search (type={kind})",
        parse=lambda b: _parse(b, query, store), store=store, limiter=limiter, use_cache=use_cache,
        corpus=("CourtListener: federal + some state courts. RECAP holds only what a user "
                "has purchased from PACER and uploaded."),
        exact_match_supported=query.strip().startswith('"'),
        not_searched=["state courts absent from CourtListener",
                      "PACER documents nobody has purchased into RECAP",
                      "sealed and expunged matters"],
        caveats=([] if query.strip().startswith('"') else
                 ["UNQUOTED query: CourtListener tokenises, so this searched for the WORDS, "
                  "not the phrase. Re-run quoted before relying on this negative."]),
        cache_params={"kind": kind, "court": court, "cursor": cursor}, headers=_headers(),
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
    info = archive(data, filename, storage_url, "evidence-search:courtlistener")
    return Hit(query=filename,
               coverage=Coverage(queried=[SOURCE], responsive=[SOURCE]),
               results=[Result(url=storage_url, title=filename, source="courtlistener",
                               engines=["courtlistener"], meta={"archived": info})])
