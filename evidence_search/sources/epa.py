"""EPA ECHO (Enforcement and Compliance History Online) client (`evidence-search epa`).

Queries `https://echodata.epa.gov/echo/echo_rest_services.get_facilities` for
EPA-regulated facilities (1.5M+ Clean Air, Clean Water, and RCRA hazardous-waste
sites), highlighting quarters in non-compliance (`FacQtrsWithNC`), formal
enforcement actions, and assessed penalties.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import Result
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import Store

SOURCE = "epa"
API_URL = "https://echodata.epa.gov/echo/echo_rest_services.get_facilities"
QID_URL = "https://echodata.epa.gov/echo/echo_rest_services.get_qid"

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="EPA ECHO regulated facilities registry (CAA, CWA, RCRA, SDWA).",
    not_searched=(
        "Unregulated facilities or small generators exempt from EPA ECHO reporting",
        "State-only environmental citations not reported to EPA ICIS / ECHO",
    ),
    caveats=(
        "Facility names reflect permit holder spellings (e.g. site operator vs parent holding company); check both street address and parent name.",
    ),
)


def _parse_epa(body: str, query: str, limit: int, store: Store | None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"EPA ECHO returned non-JSON: {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("Results"), dict):
        raise ValueError("EPA ECHO response missing `Results` object")
    res_block = data["Results"]
    if res_block.get("Error"):
        raise ValueError(f"EPA ECHO error: {res_block['Error']}")

    if "Facilities" not in res_block and ("QueryID" in res_block or "QueryRows" in res_block):
        try:
            qrows = int(res_block.get("QueryRows") or 0)
        except (TypeError, ValueError):
            qrows = 0
        if qrows == 0:
            return RecognizedEmpty("EPA ECHO QueryRows: 0")
        qid = str(res_block.get("QueryID") or "").strip()
        if not qid:
            raise ValueError("EPA ECHO response missing Facilities and QueryID")
        qid_url = f"{QID_URL}?{urlencode({'output': 'JSON', 'qid': qid, 'responseset': str(max(1, min(limit, 100)))})}"
        qid_body, qid_err = fetch(qid_url, source=SOURCE, query=query,
                                  headers={"Accept": "application/json"})
        if qid_err or not qid_body:
            raise ValueError(f"EPA ECHO get_qid failed: {getattr(qid_err, 'detail', 'empty response')}")
        try:
            qid_data = json.loads(qid_body)
        except json.JSONDecodeError as e:
            raise ValueError(f"EPA ECHO get_qid returned non-JSON: {e}") from e
        if not isinstance(qid_data, dict) or not isinstance(qid_data.get("Results"), dict):
            raise ValueError("EPA ECHO get_qid response missing `Results` object")
        res_block = qid_data["Results"]

    rows = json_rows(res_block, "Facilities", total_key=None)
    if isinstance(rows, RecognizedEmpty):
        return rows

    total = len(rows)
    try:
        qr = int(res_block.get("QueryRows") or total)
        if qr >= total:
            total = qr
    except (TypeError, ValueError):
        pass

    out: list[Result] = []
    for fac in rows[:max(1, limit)]:
        if not isinstance(fac, dict):
            continue
        reg_id = str(fac.get("RegistryID") or "").strip()
        name = str(fac.get("FacName") or "(unnamed facility)").strip()
        street = str(fac.get("FacStreet") or "").strip()
        city = str(fac.get("FacCity") or "").strip()
        state = str(fac.get("FacState") or "").strip()
        zip_c = str(fac.get("FacZip") or "").strip()
        qnc = fac.get("FacQtrsWithNC") or 0
        try:
            qnc_int = int(qnc)
        except (TypeError, ValueError):
            qnc_int = 0
        actions = fac.get("FacFormalActionCount") or 0
        penalties = fac.get("FacTotalPenalties") or "$0"
        dfr_url = (
            f"https://echo.epa.gov/detailed-facility-report?fid={reg_id}"
            if reg_id else "https://echo.epa.gov/"
        )
        rid = f"{SOURCE}:{reg_id}" if reg_id else None
        if store is not None and rid:
            try:
                store.put_record(rid, SOURCE, query, fac)
            except Exception:
                pass
        loc_str = ", ".join(p for p in (street, city, f"{state} {zip_c}".strip()) if p)
        title = f"[{state or 'US'}] {name}" + (f" (FRS {reg_id})" if reg_id else "")
        snippet = " | ".join(p for p in (
            loc_str,
            f"Qtrs w/ Non-Compliance: {qnc_int}",
            f"Formal Actions: {actions}",
            f"Penalties: {penalties}",
        ) if p)
        caveat = (
            f"SIGNIFICANT COMPLIANCE HISTORY: {qnc_int} quarter(s) with non-compliance, "
            f"{actions} formal action(s), penalties {penalties}."
            if (qnc_int > 0 or str(actions) not in ("0", "", "None")) else None
        )
        out.append(Result(
            url=dfr_url,
            title=title[:180],
            snippet=snippet[:360],
            source=SOURCE,
            engines=[SOURCE],
            meta={
                "record_id": rid,
                "registry_id": reg_id,
                "facility_name": name,
                "address": loc_str,
                "state": state,
                "qtrs_with_noncompliance": qnc_int,
                "formal_action_count": actions,
                "total_penalties": penalties,
                "entity_caveat": caveat,
                "total_matches": total,
            },
        ))
    return out


def search(query: str = "", *, state: str | None = None, zip_code: str | None = None,
           violators_only: bool = False, limit: int = 25,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Query EPA ECHO facility compliance & enforcement database."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    q_clean = (query or "").strip()
    params: dict[str, str] = {"output": "JSON"}
    if q_clean:
        params["p_fn"] = q_clean
    if state:
        params["p_st"] = state.strip().upper()
    if zip_code:
        params["p_zip"] = zip_code.strip()
    if violators_only:
        params["p_ncs"] = "Y"

    url = f"{API_URL}?{urlencode(params)}"
    label_parts = [p for p in (
        q_clean,
        f"state={state}" if state else "",
        f"zip={zip_code}" if zip_code else "",
        "violators_only" if violators_only else "",
    ) if p]
    label = " ".join(label_parts) or "*"

    return run_source(
        label,
        source=SOURCE,
        url=url,
        parse=lambda body: _parse_epa(body, label, limit, store),
        searched=f"EPA ECHO ({label})",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers={"Accept": "application/json"},
        cache_params={**params, "limit": limit},
        exact_match_supported=False,
        corpus=CORPUS,
        not_searched=list(NOT_SEARCHED),
        caveats=list(STANDING_CAVEATS),
    )
