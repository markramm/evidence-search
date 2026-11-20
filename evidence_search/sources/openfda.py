"""OpenFDA medical-device recalls, 510(k) clearances, enforcement, and adverse events (`evidence-search openfda`).

Queries `https://api.fda.gov/{device/recall,device/510k,drug/enforcement,drug/event}.json`.
Returns real countable totals (`meta.results.total`) and explicitly treats
OpenFDA's documented `{"error": {"code": "NOT_FOUND"}}` envelope as a
`RecognizedEmpty` verified absence.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import AccessBlocker, Blocker, Coverage, Result
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import Store

SOURCE = "openfda"
ENDPOINTS = {
    "recall": "https://api.fda.gov/device/recall.json",
    "510k": "https://api.fda.gov/device/510k.json",
    "enforcement": "https://api.fda.gov/drug/enforcement.json",
    "drug_event": "https://api.fda.gov/drug/event.json",
}

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="OpenFDA regulatory databases (medical device recalls, 510(k) clearances, drug enforcement reports, FAERS).",
    not_searched=(
        "Pending premarket approvals (PMA) or unreleased FDA 483 inspection observations not indexed in OpenFDA",
        "Under-reported post-market events not submitted to FDA FAERS / MAUDE",
    ),
    caveats=(
        "OpenFDA search expressions use Lucene syntax; entity names (subsidiaries, DBA names) vary across filings.",
    ),
)


def _parse_openfda(body: str, endpoint: str, query: str, store: Store | None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"OpenFDA returned non-JSON: {e}") from e

    if isinstance(data, dict) and isinstance(data.get("error"), dict):
        if data["error"].get("code") == "NOT_FOUND":
            return RecognizedEmpty(f"openfda {endpoint}: NOT_FOUND")
        raise ValueError(f"OpenFDA error: {data['error'].get('message') or data['error']}")

    rows = json_rows(data, "results", total_key=None)
    if isinstance(rows, RecognizedEmpty):
        return rows

    meta_total = ((data.get("meta") or {}).get("results") or {}).get("total")
    total = int(meta_total) if isinstance(meta_total, int) else len(rows)

    out: list[Result] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rec_num = str(
            row.get("k_number")
            or row.get("recall_number")
            or row.get("product_res_number")
            or row.get("safetyreportid")
            or ""
        ).strip()
        firm = str(
            row.get("recalling_firm")
            or row.get("applicant")
            or row.get("company_name")
            or ""
        ).strip()
        prod = str(
            row.get("product_description")
            or row.get("device_name")
            or row.get("openfda", {}).get("brand_name", [""])[0] if isinstance(row.get("openfda"), dict) else ""
        ).strip()
        if not prod:
            prod = str(row.get("device_name") or row.get("product_description") or "").strip()
        reason = str(
            row.get("reason_for_recall")
            or row.get("root_cause_description")
            or row.get("decision_description")
            or ""
        ).strip()
        dt = str(
            row.get("event_date_initiated")
            or row.get("decision_date")
            or row.get("recall_initiation_date")
            or row.get("receivedate")
            or ""
        ).strip()
        rid = f"{SOURCE}:{endpoint}:{rec_num}" if rec_num else None
        if store is not None and rid:
            try:
                store.put_record(rid, SOURCE, query, row)
            except Exception:
                pass
        title = f"[FDA {endpoint} {rec_num}] {firm or '(firm)'} — {prod[:90] or reason[:90]}"
        snippet = " | ".join(p for p in (
            f"date: {dt}" if dt else "",
            reason[:220] if reason else "",
        ) if p)
        url = (
            f"https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm?ID={rec_num}"
            if endpoint == "510k" and rec_num else ENDPOINTS[endpoint]
        )
        out.append(Result(
            url=url,
            title=title[:180],
            snippet=snippet[:360],
            source=SOURCE,
            engines=[f"{SOURCE}:{endpoint}"],
            meta={
                "record_id": rid,
                "endpoint": endpoint,
                "id": rec_num,
                "firm": firm,
                "product": prod,
                "reason": reason,
                "date": dt,
                "total_matches": total,
            },
        ))
    return out


def search(query: str = "", *, endpoint: str = "recall", firm: str | None = None,
           limit: int = 25, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Query OpenFDA `recall`, `510k`, `enforcement`, or `drug_event`."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    if endpoint not in ENDPOINTS:
        return AccessBlocker(
            query=query,
            coverage=Coverage(queried=[SOURCE], errored={SOURCE: "bad-endpoint"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url=ENDPOINTS["recall"],
            detail=f"Unknown openfda endpoint {endpoint!r}; choose from {', '.join(sorted(ENDPOINTS))}.",
        )

    q_clean = (query or "").strip()
    clauses: list[str] = []
    if firm:
        field = "applicant" if endpoint == "510k" else "recalling_firm"
        clauses.append(f'{field}:"{firm.strip()}"')
    if q_clean:
        clauses.append(q_clean)
    params = {"search": " AND ".join(clauses), "limit": str(max(1, min(limit, 100)))} if clauses else {
        "limit": str(max(1, min(limit, 100)))
    }
    url = f"{ENDPOINTS[endpoint]}?{urlencode(params)}"
    label = f"[{endpoint}] " + (" ".join(clauses) or "*")

    return run_source(
        label,
        source=SOURCE,
        url=url,
        parse=lambda body: _parse_openfda(body, endpoint, label, store),
        searched=f"OpenFDA {endpoint} ({label})",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers={"Accept": "application/json"},
        cache_params={"endpoint": endpoint, **params},
        answer_on={404: '{"error":{"code":"NOT_FOUND"}}'},
        exact_match_supported=bool(firm and not q_clean),
        corpus=CORPUS,
        not_searched=list(NOT_SEARCHED),
        caveats=list(STANDING_CAVEATS),
    )
