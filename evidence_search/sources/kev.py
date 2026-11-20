"""CISA Known Exploited Vulnerabilities (KEV) & NIST NVD CVE v2 client (`evidence-search kev`).

Answers procurement & cybersecurity accountability questions:
  * "Was the vendor's product on CISA's mandatory federal remediation catalog
    before the agency awarded or renewed the contract?"
  * Default mode queries CISA's authoritative keyless KEV catalog JSON and filters
    across CVE ID, vendorProject, product, vulnerabilityName, and
    knownRansomwareCampaignUse.
  * `--nvd` mode queries the NIST NVD CVE 2.0 REST API (`?cveId=...` or
    `?keywordSearch=...`).
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import Result
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import Store

SOURCE = "kev"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="CISA Known Exploited Vulnerabilities (KEV) catalog and NIST NVD CVE 2.0 index.",
    not_searched=(
        "Unpatched zero-days or proprietary security advisories without a CVE identifier",
        "Vulnerabilities not designated under CISA Binding Operational Directive 22-01 (when querying KEV without `--nvd`)",
    ),
    caveats=(
        "Vendor and product names in CISA KEV vary (e.g. 'Ivanti' vs 'Pulse Secure'); check both `--vendor` and keyword query.",
    ),
)


def _parse_kev(body: str, *, query: str, vendor: str | None, product: str | None,
               cve_id: str | None, ransomware_only: bool, limit: int,
               store: Store | None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"CISA KEV returned non-JSON: {e}") from e

    rows = json_rows(data, "vulnerabilities", total_key=None)
    if isinstance(rows, RecognizedEmpty):
        return rows

    q_low = (query or "").strip().lower()
    v_low = (vendor or "").strip().lower()
    p_low = (product or "").strip().lower()
    c_up = (cve_id or "").strip().upper()

    matched: list[dict] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        cve = str(item.get("cveID") or "").strip().upper()
        vp = str(item.get("vendorProject") or "").strip()
        pr = str(item.get("product") or "").strip()
        vname = str(item.get("vulnerabilityName") or "").strip()
        desc = str(item.get("shortDescription") or "").strip()
        ransom = str(item.get("knownRansomwareCampaignUse") or "").strip()

        if c_up and cve != c_up:
            continue
        if v_low and v_low not in vp.lower():
            continue
        if p_low and p_low not in pr.lower():
            continue
        if ransomware_only and ransom.lower() != "known":
            continue
        if q_low:
            hay = " ".join((cve, vp, pr, vname, desc)).lower()
            if q_low not in hay:
                continue
        matched.append(item)

    if not matched:
        return RecognizedEmpty("cisa kev catalog filtered: 0 matches")

    total = len(matched)
    out: list[Result] = []
    for item in matched[:max(1, limit)]:
        cve = str(item.get("cveID") or "").strip()
        vp = str(item.get("vendorProject") or "").strip()
        pr = str(item.get("product") or "").strip()
        vname = str(item.get("vulnerabilityName") or "").strip()
        added = str(item.get("dateAdded") or "").strip()
        due = str(item.get("dueDate") or "").strip()
        ransom = str(item.get("knownRansomwareCampaignUse") or "Unknown").strip()
        action = str(item.get("requiredAction") or "").strip()
        rid = f"{SOURCE}:{cve}" if cve else None
        if store is not None and rid:
            try:
                store.put_record(rid, SOURCE, query or cve, item)
            except Exception:
                pass
        title = f"[{cve}] {vp} {pr}" + (f" — {vname}" if vname else "")
        snippet = " | ".join(p for p in (
            f"added: {added}" if added else "",
            f"due: {due}" if due else "",
            f"ransomware: {ransom}",
            action[:140] if action else "",
        ) if p)
        out.append(Result(
            url=f"https://nvd.nist.gov/vuln/detail/{cve}" if cve else KEV_URL,
            title=title[:180],
            snippet=snippet[:360],
            source=SOURCE,
            engines=[f"{SOURCE}:cisa"],
            meta={
                "record_id": rid,
                "cve_id": cve,
                "vendor": vp,
                "product": pr,
                "vulnerability_name": vname,
                "date_added": added,
                "due_date": due,
                "ransomware_use": ransom,
                "required_action": action,
                "total_matches": total,
            },
        ))
    return out


def _parse_nvd(body: str, query: str, store: Store | None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"NIST NVD returned non-JSON: {e}") from e

    rows = json_rows(data, "vulnerabilities", total_key="totalResults")
    if isinstance(rows, RecognizedEmpty):
        return rows

    total = data.get("totalResults")
    if not isinstance(total, int):
        total = len(rows)

    out: list[Result] = []
    for wrap in rows:
        if not isinstance(wrap, dict):
            continue
        cve_obj = wrap.get("cve") if isinstance(wrap.get("cve"), dict) else wrap
        if not isinstance(cve_obj, dict):
            continue
        cve = str(cve_obj.get("id") or "").strip()
        pub = str(cve_obj.get("published") or "")[:10]
        descs = cve_obj.get("descriptions") or []
        desc = ""
        for d in descs:
            if isinstance(d, dict) and d.get("lang") == "en":
                desc = str(d.get("value") or "").strip()
                break
        if not desc and descs and isinstance(descs[0], dict):
            desc = str(descs[0].get("value") or "").strip()

        metrics = cve_obj.get("metrics") if isinstance(cve_obj.get("metrics"), dict) else {}
        cvss_score = None
        severity = ""
        for mk in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV40", "cvssMetricV2"):
            mlist = metrics.get(mk)
            if isinstance(mlist, list) and mlist and isinstance(mlist[0], dict):
                cd = mlist[0].get("cvssData") if isinstance(mlist[0].get("cvssData"), dict) else {}
                cvss_score = cd.get("baseScore")
                severity = str(mlist[0].get("baseSeverity") or cd.get("baseSeverity") or "")
                break

        rid = f"{SOURCE}:nvd:{cve}" if cve else None
        if store is not None and rid:
            try:
                store.put_record(rid, SOURCE, query, cve_obj)
            except Exception:
                pass
        title = f"[{cve}]" + (f" ({severity} {cvss_score})" if cvss_score is not None else "") + f" {desc[:110]}"
        out.append(Result(
            url=f"https://nvd.nist.gov/vuln/detail/{cve}" if cve else NVD_URL,
            title=title[:180],
            snippet=f"published: {pub} | {desc[:280]}" if pub else desc[:320],
            source=SOURCE,
            engines=[f"{SOURCE}:nvd"],
            meta={
                "record_id": rid,
                "cve_id": cve,
                "published": pub,
                "cvss_score": cvss_score,
                "severity": severity,
                "description": desc,
                "total_matches": total,
            },
        ))
    return out


def search(query: str = "", *, vendor: str | None = None, product: str | None = None,
           cve_id: str | None = None, ransomware_only: bool = False,
           nvd: bool = False, limit: int = 25,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search CISA KEV catalog (default) or NIST NVD CVE v2 (`nvd=True`)."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    q_clean = (query or "").strip()
    c_clean = (cve_id or "").strip().upper() or None
    if c_clean is None and q_clean.upper().startswith("CVE-"):
        c_clean = q_clean.upper()

    label_parts = [p for p in (
        q_clean,
        f"vendor={vendor}" if vendor else "",
        f"product={product}" if product else "",
        f"cve={c_clean}" if c_clean and c_clean != q_clean.upper() else "",
        "ransomware" if ransomware_only else "",
        "nvd" if nvd else "kev",
    ) if p]
    label = " ".join(label_parts) or "*"

    if nvd:
        params: dict[str, str] = {"resultsPerPage": str(max(1, min(limit, 200)))}
        if c_clean:
            params["cveId"] = c_clean
        elif q_clean or vendor or product:
            params["keywordSearch"] = " ".join(x for x in (q_clean, vendor or "", product or "") if x).strip()
        url = f"{NVD_URL}?{urlencode(params)}"
        return run_source(
            label,
            source=SOURCE,
            url=url,
            parse=lambda body: _parse_nvd(body, label, store),
            searched=f"NIST NVD CVE 2.0 ({label})",
            store=store,
            limiter=limiter,
            use_cache=use_cache,
            headers={"Accept": "application/json"},
            cache_params=params,
            exact_match_supported=bool(c_clean),
            corpus=CORPUS,
            not_searched=list(NOT_SEARCHED),
            caveats=list(STANDING_CAVEATS),
        )

    return run_source(
        label,
        source=SOURCE,
        url=KEV_URL,
        parse=lambda body: _parse_kev(
            body, query=q_clean, vendor=vendor, product=product,
            cve_id=c_clean, ransomware_only=ransomware_only, limit=limit, store=store,
        ),
        searched=f"CISA KEV ({label})",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers={"Accept": "application/json"},
        cache_params={
            "q": q_clean, "vendor": vendor or "", "product": product or "",
            "cve": c_clean or "", "ransomware": ransomware_only, "limit": limit,
        },
        exact_match_supported=bool(c_clean),
        corpus=CORPUS,
        not_searched=list(NOT_SEARCHED),
        caveats=list(STANDING_CAVEATS),
    )
