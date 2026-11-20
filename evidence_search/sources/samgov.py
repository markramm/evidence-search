"""SAM.gov — federal contract opportunities, solicitations, and public attachments (#2).

Verified live 2026-08-19 and 2026-10-08:
  * Opportunity search (keyless JSON):
    `https://sam.gov/api/prod/sgs/v1/search/?index=opp&q=<term>&page=0&size=<n>`
  * Notice detail (keyless JSON):
    `https://sam.gov/api/prod/opps/v2/opportunities/<id>`
  * Public attachments (keyless JSON + 303 redirect to signed S3 URL):
    `https://sam.gov/api/prod/opps/v3/opportunities/<id>/resources`

The research question this answers: *is this contract action public on SAM.gov
(an RFP, RFI, sole-source J&A, or award notice), or do I need to file a FOIA?*
And it is the missing half of USAspending (#17): when a researcher queries a
solicitation PIID (9th character `Q` or `R`) on `usaspending`, it points them
here.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode

from bs4 import BeautifulSoup

from ..core.archive import archive
from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import AccessBlocker, Blocker, Coverage, Hit, RateLimited, Result
from ..core.source import RecognizedEmpty, Unanswerable, run_source
from ..core.store import Store

SOURCE = "samgov"
SEARCH_API = "https://sam.gov/api/prod/sgs/v1/search/"
DETAIL_API = "https://sam.gov/api/prod/opps/v2/opportunities"
RESOURCES_API = "https://sam.gov/api/prod/opps/v3/opportunities"


def _strip_html(s: str) -> str:
    if not s:
        return ""
    if "<" not in s:
        return re.sub(r"\s+", " ", s).strip()
    return re.sub(r"\s+", " ", BeautifulSoup(s, "html.parser").get_text(" ")).strip()


def _parse_search(body: str, query: str, page: int, store: Store | None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"non-JSON response: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")

    emb = data.get("_embedded")
    pg = data.get("page")
    if not isinstance(emb, dict) and not isinstance(pg, dict):
        raise ValueError(
            "no `_embedded` or `page` in SAM.gov response "
            f"(keys: {', '.join(sorted(map(str, data))) or 'none'})")

    rows = (emb or {}).get("results") if isinstance(emb, dict) else None
    total = (pg or {}).get("totalElements") if isinstance(pg, dict) else None
    if rows is None and total == 0:
        return RecognizedEmpty("_embedded.results: [], page.totalElements: 0")
    if not isinstance(rows, list):
        raise ValueError("no `_embedded.results` list in SAM.gov response")
    if not rows:
        if isinstance(total, bool) or (total is not None and not isinstance(total, int)):
            raise ValueError(f"unreadable `page.totalElements`: {total!r}")
        if isinstance(total, int) and total > 0:
            raise Unanswerable(
                f"SAM.gov reports totalElements={total} but page {page} carries no rows "
                "-- a page past the end, NOT an absence. Re-run with --page 1.")
        return RecognizedEmpty("_embedded.results: []" + (f", page.totalElements: {total}"
                                                          if total is not None else ""))

    out: list[Result] = []
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError(f"a result row is {type(r).__name__}, not an object")
        opp_id = str(r.get("_id") or r.get("opportunityId") or r.get("noticeId") or "")
        sol_num = (r.get("solicitationNumber") or r.get("cleanSolicitationNumber") or "").strip()
        title = _strip_html(r.get("title") or "") or sol_num or opp_id
        ntype = ""
        if isinstance(r.get("type"), dict):
            ntype = r["type"].get("value") or r["type"].get("code") or ""
        elif r.get("type"):
            ntype = str(r.get("type"))
        orgs = r.get("organizationHierarchy") or []
        agency = " > ".join(o.get("name", "") for o in orgs[:2] if isinstance(o, dict) and o.get("name"))
        if not agency:
            agency = str(r.get("fullParentPathName") or r.get("agency") or "")
        pub_date = (r.get("publishDate") or r.get("modifiedDate") or "")[:10]
        resp_date = (r.get("responseDate") or "")[:10]
        active = r.get("isActive")
        desc = ""
        if isinstance(r.get("descriptions"), list) and r["descriptions"]:
            first_d = r["descriptions"][0]
            desc = _strip_html(first_d.get("content", "") if isinstance(first_d, dict) else str(first_d))
        elif r.get("description"):
            desc = _strip_html(str(r["description"]))

        rid = f"{SOURCE}:{opp_id or sol_num}"
        if store is not None:
            try:
                store.put_record(rid, SOURCE, query, r)
            except Exception:
                pass

        snip_bits = [b for b in (
            sol_num,
            ntype,
            agency,
            f"published {pub_date}" if pub_date else "",
            f"due {resp_date}" if resp_date else "",
            desc[:120] if desc else "",
        ) if b]

        out.append(Result(
            url=f"https://sam.gov/opp/{opp_id}/view" if opp_id else f"https://sam.gov/search/?q={quote(query)}",
            title=f"{title}" + (f" [{sol_num}]" if sol_num and sol_num not in title else ""),
            snippet=" | ".join(snip_bits),
            source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
            meta={
                "record_id": rid,
                "opportunity_id": opp_id,
                "solicitation_number": sol_num or None,
                "notice_type": ntype or None,
                "agency": agency or None,
                "publish_date": pub_date or None,
                "response_date": resp_date or None,
                "is_active": active,
                "naics": r.get("naics") or r.get("naicsCode"),
                "psc": r.get("classificationCode") or r.get("psc"),
                "description": desc[:500] if desc else None,
                "total_matches": total,
                "page": page,
            },
        ))
    return out


def search(query: str, *, active_only: bool = False, notice_type: str | None = None,
           limit: int = 25, page: int = 1,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search SAM.gov contract opportunities."""
    store = store or Store()
    api_page = max(0, page - 1)
    params = [
        ("index", "opp"),
        ("q", query),
        ("page", str(api_page)),
        ("size", str(limit)),
        ("sort", "-modifiedDate"),
        ("mode", "search"),
        ("is_active", "true" if active_only else "false"),
    ]
    if active_only:
        params = [p for p in params if p[0] != "is_active"] + [("is_active", "true")]
    else:
        params = [p for p in params if p[0] != "is_active"]
    if notice_type:
        params.append(("notice_type", notice_type))

    url = f"{SEARCH_API}?{urlencode(params)}"
    exact = query.strip().startswith('"') and query.strip().endswith('"')
    return run_source(
        query, source=SOURCE, url=url,
        searched="sam.gov contract opportunities index",
        parse=lambda b: _parse_search(b, query, page, store),
        store=store, limiter=limiter, use_cache=use_cache,
        exact_match_supported=exact,
        corpus="SAM.gov contract opportunities: presolicitations, RFPs, RFIs, J&As, and award notices.",
        not_searched=[
            "executed prime awards and task orders (use `usaspending`)",
            "GSA eBuy-only task order RFQs not cross-posted to public SAM.gov",
            "classified or national-security-exempt procurements",
            "SAM.gov entity-registration detail pages (JS-only SPA; use `browser` tier)",
        ],
        caveats=([] if exact else [
            "UNQUOTED query: SAM.gov matches keywords across notice text. "
            "Quote a solicitation number or exact phrase before relying on an absence."
        ]) + (["Filtered to active opportunities only (--active-only); archived/closed notices were excluded."]
              if active_only else []),
        cache_params={"active_only": active_only, "notice_type": notice_type,
                      "limit": limit, "page": page},
    )


def detail(opp_id: str, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Fetch full notice body and metadata for one SAM.gov opportunity ID (#2).

    Solicitation narratives on SAM.gov frequently name the facility, unit, and
    scope of work -- serving as a direct substitute for a FOIA request.
    """
    store = store or Store()
    clean_id = (opp_id or "").strip().removeprefix(f"{SOURCE}:").strip()
    url = f"{DETAIL_API}/{quote(clean_id, safe='')}"

    def _parse_detail(body: str) -> list[Result]:
        try:
            d = json.loads(body)
        except json.JSONDecodeError as e:
            raise ValueError(f"non-JSON response: {e}") from e
        if not isinstance(d, dict) or not (d.get("opportunityId") or d.get("noticeId") or d.get("data") or d.get("title")):
            raise ValueError("unrecognised SAM.gov opportunity detail payload")
        payload = d.get("data") if isinstance(d.get("data"), dict) else d
        oid = str(payload.get("opportunityId") or payload.get("noticeId") or clean_id)
        sol = payload.get("solicitationNumber") or ""
        title = _strip_html(payload.get("title") or "") or sol or oid
        desc_raw = ""
        if isinstance(payload.get("description"), list) and payload["description"]:
            first = payload["description"][0]
            desc_raw = first.get("body", "") if isinstance(first, dict) else str(first)
        elif isinstance(payload.get("description"), str):
            desc_raw = payload["description"]
        desc = _strip_html(desc_raw)
        psc = payload.get("classificationCode") or payload.get("psc")
        naics = payload.get("naicsCode") or payload.get("naics")
        rid = f"{SOURCE}:detail:{oid}"
        try:
            store.put_record(rid, SOURCE, clean_id, d)
        except Exception:
            pass
        return [Result(
            url=f"https://sam.gov/opp/{oid}/view",
            title=f"{title}" + (f" [{sol}]" if sol else ""),
            snippet=" | ".join(x for x in (
                f"PSC: {psc or '(none in record)'}",
                f"NAICS: {naics or '(none in record)'}",
                f"DESC: {desc[:240]}" if desc else "DESC: (none in record)",
            ) if x),
            source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
            meta={
                "record_id": rid,
                "opportunity_id": oid,
                "solicitation_number": sol or None,
                "title": title,
                "psc": psc,
                "naics": naics,
                "description": desc,
                "point_of_contact": payload.get("pointOfContact"),
                "place_of_performance": payload.get("placeOfPerformance"),
                "award": payload.get("award"),
            },
        )]

    return run_source(
        clean_id, source=SOURCE, url=url,
        searched=f"sam.gov opportunity detail {clean_id}",
        parse=_parse_detail,
        archive_as=f"samgov-opp-{clean_id}.json",
        store=store, limiter=limiter, use_cache=use_cache,
        answer_on={Blocker.NOT_FOUND: (
            "Rests on an HTTP 404 for this internal SAM.gov opportunity ID. "
            "If you passed a solicitation number (PIID) rather than the hex "
            "opportunity ID from search results, run `evidence-search samgov '<PIID>'` first.")},
        exact_match_supported=True,
        corpus="SAM.gov opportunity notice detail endpoint.",
        not_searched=["notices known only by solicitation number unless resolved via search first"],
        cache_params={"mode": "detail"},
    )


def attachments(opp_id: str, download: bool = False,
                store: Store | None = None, limiter: Limiter | None = None,
                use_cache: bool = True):
    """List (and optionally download & archive) public attachments on a SAM.gov
    opportunity (`opps/v3/opportunities/<id>/resources`, verified 2026-10-08).
    """
    store = store or Store()
    limiter = limiter or Limiter(store)
    clean_id = (opp_id or "").strip().removeprefix(f"{SOURCE}:").strip()
    url = f"{RESOURCES_API}/{quote(clean_id, safe='')}/resources"

    def _parse_resources(body: str) -> list[Result]:
        try:
            d = json.loads(body)
        except json.JSONDecodeError as e:
            raise ValueError(f"non-JSON response: {e}") from e
        if not isinstance(d, dict):
            raise ValueError(f"expected a JSON object, got {type(d).__name__}")
        emb = d.get("_embedded")
        if emb is None and "opportunityAttachmentList" not in d:
            raise ValueError("no `_embedded` or `opportunityAttachmentList` in SAM.gov resources response")
        att_groups = (emb or {}).get("opportunityAttachmentList") or d.get("opportunityAttachmentList") or []
        if not isinstance(att_groups, list):
            raise ValueError("`opportunityAttachmentList` is not a list")
        files: list[dict] = []
        for g in att_groups:
            if isinstance(g, dict) and isinstance(g.get("attachments"), list):
                for a in g["attachments"]:
                    if isinstance(a, dict) and not a.get("deletedFlag"):
                        files.append(a)
            elif isinstance(g, dict) and g.get("resourceId"):
                files.append(g)
        if not files:
            return RecognizedEmpty("opportunityAttachmentList: [] (no attachments on notice)")
        out: list[Result] = []
        for f in files:
            res_id = str(f.get("resourceId") or f.get("attachmentId") or "")
            name = f.get("name") or f.get("description") or res_id
            access = f.get("accessLevel") or "public"
            size = f.get("size")
            dl_url = f"{RESOURCES_API}/files/{quote(res_id, safe='')}/download" if res_id else ""
            archived_info = None
            if download and dl_url and access == "public":
                ok, _, _ = limiter.reserve(SOURCE)
                if ok:
                    raw_bytes, berr = fetch(dl_url, source=SOURCE, query=name, binary=True)
                    limiter.note_outcome(SOURCE, berr)
                    if raw_bytes and not berr:
                        archived_info = archive(
                            raw_bytes if isinstance(raw_bytes, bytes) else str(raw_bytes).encode(),
                            f"samgov-{clean_id}-{name}", dl_url, "evidence-search:samgov")
            out.append(Result(
                url=dl_url or url,
                title=f"{name} ({access})",
                snippet=" | ".join(x for x in (
                    f"access: {access}",
                    f"{size:,} bytes" if isinstance(size, int) else None,
                    f"archived: {archived_info['path']}" if archived_info else None,
                ) if x),
                source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
                meta={"resource_id": res_id, "name": name, "access_level": access,
                      "size": size, "download_url": dl_url, "archived": archived_info},
            ))
        return out

    return run_source(
        clean_id, source=SOURCE, url=url,
        searched=f"sam.gov attachments for opportunity {clean_id}",
        parse=_parse_resources,
        store=store, limiter=limiter, use_cache=use_cache and not download,
        exact_match_supported=True,
        corpus=f"SAM.gov attachment resources for opportunity {clean_id}.",
        not_searched=["controlled/export-restricted attachments requiring a logged-in SAM.gov vendor account"],
        cache_params={"mode": "attachments"},
    )
