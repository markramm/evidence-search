"""CFPB Consumer Complaint Database client (`evidence-search cfpb`).

Queries `https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/`
for consumer complaints and narratives against debt collectors, prison/detention
payment processors, student-loan servicers, and fintech/banking contractors.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import Result
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import Store

SOURCE = "cfpb"
API_URL = "https://www.consumerfinance.gov/data-research/consumer-complaints/search/api/v1/"

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="CFPB public Consumer Complaint Database (2011-present).",
    not_searched=(
        "Complaints sent to state AGs or FTC Sentinel without forwarding to CFPB",
        "Unverified narratives withheld by consumers (consumers must opt in for narrative publication)",
    ),
    caveats=(
        "Complaint narratives are unverified consumer accounts published after company verification of a commercial relationship.",
    ),
)


def _parse_cfpb(body: str, query: str, store: Store | None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"CFPB API returned non-JSON: {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("hits"), dict):
        raise ValueError("CFPB API response missing `hits` object")

    hits_block = data["hits"]
    rows = json_rows(hits_block, "hits", total_key=None)
    if isinstance(rows, RecognizedEmpty):
        return rows

    tot_raw = hits_block.get("total")
    if isinstance(tot_raw, dict):
        total = tot_raw.get("value", len(rows))
    elif isinstance(tot_raw, int):
        total = tot_raw
    else:
        total = len(rows)

    out: list[Result] = []
    for h in rows:
        if not isinstance(h, dict):
            continue
        src = h.get("_source") if isinstance(h.get("_source"), dict) else h
        if not isinstance(src, dict):
            continue
        cid = str(src.get("complaint_id") or h.get("_id") or "").strip()
        company = str(src.get("company") or "").strip()
        product = str(src.get("product") or "").strip()
        issue = str(src.get("issue") or "").strip()
        dt = str(src.get("date_received") or "")[:10]
        state = str(src.get("state") or "").strip()
        resp = str(src.get("company_response") or "").strip()
        narrative = str(src.get("complaint_what_happened") or "").strip()

        rid = f"{SOURCE}:{cid}" if cid else None
        if store is not None and rid:
            try:
                store.put_record(rid, SOURCE, query, src)
            except Exception:
                pass

        title = f"[{dt or '?'}] {company or '(company)'} — {issue or product}"
        snippet = " | ".join(p for p in (
            f"product: {product}" if product else "",
            f"state: {state}" if state else "",
            f"response: {resp}" if resp else "",
            narrative[:140] if narrative else "",
        ) if p)
        url = (
            f"https://www.consumerfinance.gov/data-research/consumer-complaints/search/detail/{cid}"
            if cid else API_URL
        )
        out.append(Result(
            url=url,
            title=title[:180],
            snippet=snippet[:360],
            source=SOURCE,
            engines=[SOURCE],
            meta={
                "record_id": rid,
                "complaint_id": cid,
                "company": company,
                "product": product,
                "issue": issue,
                "date_received": dt,
                "state": state,
                "company_response": resp,
                "narrative": narrative,
                "total_matches": total,
            },
        ))
    return out


def search(query: str = "", *, company: str | None = None, product: str | None = None,
           state: str | None = None, limit: int = 25,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search CFPB Consumer Complaint Database."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    params: list[tuple[str, str]] = [
        ("size", str(max(1, min(limit, 100)))),
        ("sort", "created_date_desc"),
    ]
    q_clean = (query or "").strip()
    if q_clean:
        params.append(("search_term", q_clean))
    if company:
        params.append(("company", company.strip()))
    if product:
        params.append(("product", product.strip()))
    if state:
        params.append(("state", state.strip().upper()))

    url = f"{API_URL}?{urlencode(params)}"
    label_parts = [p for p in (
        q_clean,
        f"company={company}" if company else "",
        f"product={product}" if product else "",
        f"state={state}" if state else "",
    ) if p]
    label = " ".join(label_parts) or "*"

    return run_source(
        label,
        source=SOURCE,
        url=url,
        parse=lambda body: _parse_cfpb(body, label, store),
        searched=f"CFPB Consumer Complaints ({label})",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Accept-Language": None},
        cache_params={k: v for k, v in params},
        exact_match_supported=bool(company and not q_clean),
        corpus=CORPUS,
        not_searched=list(NOT_SEARCHED),
        caveats=list(STANDING_CAVEATS),
    )
