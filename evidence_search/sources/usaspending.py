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

  --recipient   `recipient_search_text` -- matches the RECIPIENT NAME. Precise
                ON CONTRACTS. On grants, loans and direct payments it is much
                FUZZIER: a worker checking a vendor found 14 "extra" assistance
                awards that were all unrelated SBA COVID-era small businesses
                with similar names -- different UEI, different state. Verify the
                UEI before attributing any assistance award to a vendor.
  --keywords    `keywords` -- full-text across award descriptions and more. Use
                for discovery. It is FUZZY: searching "Force Science" returns
                NURAD Technologies, because the words appear somewhere in the
                record. A count from a keyword search is not a count OF anything
                in particular, so the client labels it.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
from urllib.parse import quote
from typing import Any

from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, Probe, RateLimited,
                            Result, absence_meta, replay_cached, verified_absence)
from ..core.source import RecognizedEmpty, Unanswerable, json_rows, run_source
from ..core.store import Store, cache_key

SOURCE = "usaspending"
API = "https://api.usaspending.gov/api/v2"

CONTRACT_TYPES = ["A", "B", "C", "D"]
IDV_TYPES = ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C",
             "IDV_C", "IDV_D", "IDV_E"]
GRANT_TYPES = ["02", "03", "04", "05", "F001", "F002"]
LOAN_TYPES = ["07", "08", "F003", "F004"]
DIRECT_PAYMENT_TYPES = ["09", "F005", "11", "-1", "F008", "F009", "F010"]
OTHER_ASSISTANCE_TYPES = ["06", "10", "F006", "F007"]

AWARD_GROUPS = {
    "contracts": CONTRACT_TYPES,
    "idvs": IDV_TYPES,
    "grants": GRANT_TYPES,
    "loans": LOAN_TYPES,
    "direct_payments": DIRECT_PAYMENT_TYPES,
    "other_assistance": OTHER_ASSISTANCE_TYPES,
}

#: Valid ONLY on the count endpoint, which accepts a mixed list.
ALL_AWARD_TYPES = [c for codes in AWARD_GROUPS.values() for c in codes]

_PIID = re.compile(r"^[A-Z0-9]{6}\d{2}([A-Z])[A-Z0-9]{4,9}$")
_IDV_INSTRUMENTS = {"A", "B", "D"}
_SOLICITATION_INSTRUMENTS = {"Q", "R"}

#: Common agency abbreviations used on this beat, mapped to USAspending's
#: exact toptier/subtier names (#17 follow-on / FEEDBACK.md).
AGENCY_ALIASES: dict[str, tuple[str, str]] = {
    "ice": ("subtier", "U.S. Immigration and Customs Enforcement"),
    "cbp": ("subtier", "U.S. Customs and Border Protection"),
    "uscis": ("subtier", "U.S. Citizenship and Immigration Services"),
    "fema": ("subtier", "Federal Emergency Management Agency"),
    "dhs": ("toptier", "Department of Homeland Security"),
    "doj": ("toptier", "Department of Justice"),
    "bop": ("subtier", "Federal Bureau of Prisons"),
    "usms": ("subtier", "U.S. Marshals Service"),
    "dea": ("subtier", "Drug Enforcement Administration"),
    "fbi": ("subtier", "Federal Bureau of Investigation"),
    "atf": ("subtier", "Bureau of Alcohol, Tobacco, Firearms and Explosives"),
    "dod": ("toptier", "Department of Defense"),
    "army": ("subtier", "Department of the Army"),
    "navy": ("subtier", "Department of the Navy"),
    "usaf": ("subtier", "Department of the Air Force"),
    "hhs": ("toptier", "Department of Health and Human Services"),
    "acf": ("subtier", "Administration for Children and Families"),
    "gsa": ("toptier", "General Services Administration"),
    "state": ("toptier", "Department of State"),
    "dos": ("toptier", "Department of State"),
    "treasury": ("toptier", "Department of the Treasury"),
    "irs": ("subtier", "Internal Revenue Service"),
}


def resolve_agency(agency: str | None) -> dict[str, str] | None:
    """Resolve an agency abbreviation or full name into USAspending's filter shape."""
    if not agency or not agency.strip():
        return None
    raw = agency.strip()
    hit = AGENCY_ALIASES.get(raw.lower())
    if hit:
        tier, name = hit
        return {"type": "awarding", "tier": tier, "name": name}
    return {"type": "awarding", "tier": "toptier", "name": raw}


def _reporting_lag_caveat(date_from: str | None, date_to: str | None) -> list[str]:
    """Warn on date-bounded queries near the present (#17 follow-on).

    DoD awards carry an explicit 90-day FPDS/USAspending reporting lag, and
    civilian agencies can lag 2-4 weeks between award execution and publication.
    """
    if not (date_from or date_to):
        return []
    today = _dt.date.today()
    cutoff = (today - _dt.timedelta(days=90)).isoformat()
    bound = date_to or today.isoformat()
    if bound >= cutoff or (date_from and date_from >= cutoff):
        return [
            "REPORTING LAG: recent awards may not yet appear in USAspending "
            "(DoD withholds FPDS disclosures for 90 days; civilian agencies "
            "typically lag 2-4 weeks). Check SAM.gov award notices or agency "
            "press releases before publishing a recent-window absence."
        ]
    return []


def _piid_instrument(query: str) -> str | None:
    """The instrument-type letter if `query` is shaped like a PIID, else None."""
    m = _PIID.match((query or "").strip().upper().replace("-", ""))
    return m.group(1) if m else None


def _solicitation_blocker(query: str, url: str) -> AccessBlocker | None:
    """Refuse a solicitation number before spending a ledger slot on it."""
    inst = _piid_instrument(query)
    if inst not in _SOLICITATION_INSTRUMENTS:
        return None
    return AccessBlocker(
        query=query,
        coverage=Coverage(queried=[SOURCE], errored={SOURCE: "solicitation-number"}),
        mechanism=Blocker.WRONG_ID_TYPE, url=url,
        detail=(f"{query!r} is a solicitation number, not an award ID: the 9th "
                f"character {inst!r} marks a request for "
                f"{'quotations' if inst == 'Q' else 'proposals'}. USAspending holds "
                "awards only, so THIS IS NOT AN ABSENCE -- search for the "
                "solicitation on SAM.gov (sam.gov/opp), and for any award it "
                "produced by the awardee's name here."))


FIELDS = ["Award ID", "Recipient Name", "Awarding Agency", "Awarding Sub Agency",
          "Award Amount", "Total Outlays", "Start Date", "End Date",
          "Description", "Contract Award Type", "recipient_id"]

TRANSACTION_FIELDS = [
    "Transaction ID", "Award ID", "Modification Number", "Action Date",
    "Transaction Amount", "Transaction Description", "Action Type",
    "Recipient Name", "Awarding Agency", "Awarding Sub Agency",
]


def _post(path: str, body: dict, timeout: float = 45.0):
    """POST JSON. `fetch` is GET-only, so this is deliberate and local."""
    import httpx
    try:
        r = httpx.post(f"{API}{path}", json=body, timeout=timeout,
                       headers={"User-Agent": "evidence-search",
                                "Content-Type": "application/json"})
    except httpx.TimeoutException as e:
        return None, ("timeout", str(e))
    except httpx.HTTPError as e:
        return None, ("http", str(e))
    if r.status_code == 429:
        return None, ("rate", r.headers.get("Retry-After", ""))
    if r.status_code >= 400:
        return None, ("status", f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json(), None
    except json.JSONDecodeError as e:
        return None, ("parse", str(e))


def _post_transport(body: dict):
    """Adapt _post to the (payload, outcome_or_None) shape run_source expects."""
    def transport(url, *, source, query="", headers=None):
        path = url[len(API):] if url.startswith(API) else url
        data, err = _post(path, body)
        if err is None:
            return data, None
        kind, detail = err
        if kind == "rate":
            from ..core.http import retry_after_seconds
            return None, RateLimited(
                query=query, coverage=Coverage(queried=[source], errored={source: kind}),
                source=source, retry_after_s=retry_after_seconds(detail),
                detail=f"HTTP 429 (Retry-After: {detail})")
        if kind == "timeout":
            return None, RateLimited(
                query=query, coverage=Coverage(queried=[source], errored={source: kind}),
                source=source, retry_after_s=60, detail=detail)
        return None, AccessBlocker(
            query=query, coverage=Coverage(queried=[source], errored={source: kind}),
            mechanism=Blocker.SERVER_ERROR, url=url, detail=detail)
    return transport


def _keep_transport_outcome(blocked, body):
    """Return the transport's own outcome unchanged."""
    return blocked


def _filters(query: str, by_recipient: bool, award_types: list[str],
             date_from: str | None, date_to: str | None,
             agency: str | None = None) -> dict:
    f: dict[str, Any] = {"award_type_codes": award_types}
    if by_recipient:
        f["recipient_search_text"] = [query]
    else:
        f["keywords"] = [query]
    if date_from or date_to:
        f["time_period"] = [{"start_date": date_from or "2007-10-01",
                             "end_date": date_to or "2030-09-30"}]
    ag = resolve_agency(agency)
    if ag:
        f["agencies"] = [ag]
    return f


def _parse_counts(data, query: str, by_recipient: bool) -> list[Result]:
    """The count endpoint returns one summary row, not a list of awards."""
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    res = data.get("results")
    if not isinstance(res, dict) or not res:
        raise ValueError(
            "no per-group `results` counts in response "
            f"(keys: {', '.join(sorted(map(str, data))) or 'none'})")
    unreadable = [k for k, v in res.items()
                  if isinstance(v, bool) or not isinstance(v, int)]
    total = sum(v for k, v in res.items() if k not in unreadable)
    if not total:
        if unreadable:
            raise ValueError("unreadable count(s) for " + ", ".join(map(str, unreadable)))
        return RecognizedEmpty("results: every award group counted 0")

    mode = "recipient-name" if by_recipient else "keyword (FUZZY)"
    return [Result(
        url=f"https://www.usaspending.gov/search?keywords={query}",
        title=f"{total} federal awards matching {query!r}",
        snippet=" | ".join(f"{k}: {v}" for k, v in res.items() if v),
        source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
        meta={"total_awards": total, "by_type": res, "search_mode": mode,
              "dollar_total_note": ("This endpoint counts AWARDS, not dollars. For a "
                                    "dollar total use --sum, which pages the award list "
                                    "and adds Award Amount -- and report it as a floor if "
                                    "more_pages is true."),
              "count_caveat": (None if by_recipient else
                               "KEYWORD search is full-text across descriptions: this "
                               "counts records CONTAINING the words, not awards TO a "
                               "vendor. Use --recipient for a vendor total.")},
    )]


def counts(query: str, by_recipient: bool = True, award_types: list[str] | None = None,
           date_from: str | None = None, date_to: str | None = None,
           agency: str | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Real totals by award type. The countable primitive this beat needs."""
    url = f"{API}/search/spending_by_award_count/"
    blocked = _solicitation_blocker(query, url)
    if blocked:
        return blocked
    types = list(award_types or ALL_AWARD_TYPES)
    routed: list[str] = []
    inst = _piid_instrument(query)
    if inst in _IDV_INSTRUMENTS and not set(types) & set(IDV_TYPES):
        types += IDV_TYPES
        routed.append(f"{query!r} is shaped like an IDV PIID (instrument type "
                      f"{inst!r}), so IDVs were counted as well as the groups asked for.")
    excluded = [g for g, codes in AWARD_GROUPS.items() if not set(types) & set(codes)]
    mode = "recipient-name" if by_recipient else "keyword (FUZZY)"
    ag = resolve_agency(agency)
    ag_note = f", agency={ag['name']}" if ag else ""
    return run_source(
        query, source=SOURCE, url=url,
        searched=f"usaspending award counts ({mode}{ag_note})",
        parse=lambda d: _parse_counts(d, query, by_recipient),
        transport=_post_transport(
            {"filters": _filters(query, by_recipient, types, date_from, date_to, agency=agency)}),
        on_blocked=_keep_transport_outcome,
        store=store, limiter=limiter, use_cache=use_cache,
        exact_match_supported=False,
        corpus=("USAspending award counts from FY2008. Unlike a web tier this "
                "IS a measurement -- the endpoint counts the matching awards."),
        caveats=routed + _reporting_lag_caveat(date_from, date_to) + ([] if by_recipient else
                 ["KEYWORD counts records CONTAINING the words, not awards TO a "
                  "vendor. A count from a keyword search is not a count of "
                  "anything in particular."]),
        not_searched=["awards below the reporting threshold",
                      "classified and otherwise unreported spending"]
                     + ([f"award-type groups not counted: {', '.join(excluded)} "
                         "(pass --all-types to count every group)"]
                        if excluded else []),
        cache_params={"mode": "count", "recipient": by_recipient,
                      "types": ",".join(types),
                      "date_from": date_from, "date_to": date_to,
                      "agency": (ag["name"] if ag else None)},
    )


def detail(record_id: str, store: Store | None = None, limiter: Limiter | None = None):
    """Full FPDS detail for one award: PSC, NAICS, the contracting officer's own words."""
    store = store or Store()
    limiter = limiter or Limiter(store)
    num = str(record_id).split(":")[-1].strip()

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=num, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    import httpx
    url = f"{API}/awards/{quote(num, safe='')}/"
    try:
        r = httpx.get(url, timeout=45, headers={"User-Agent": "evidence-search"})
    except httpx.HTTPError as e:
        limiter.note_outcome(SOURCE, ("http", str(e)))
        return AccessBlocker(query=num, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "http"}),
                             mechanism=Blocker.SERVER_ERROR, url=url, detail=str(e))
    if r.status_code >= 500:
        limiter.note_outcome(SOURCE, ("status", f"HTTP {r.status_code}"))
    elif r.status_code < 400:
        limiter.note_outcome(SOURCE, None)
    if r.status_code == 404:
        if not num.isdigit():
            return AccessBlocker(
                query=num,
                coverage=Coverage(queried=[SOURCE], errored={SOURCE: "wrong-id-type"}),
                mechanism=Blocker.WRONG_ID_TYPE, url=url,
                detail=(f"{num!r} is not USAspending's internal numeric award id, and "
                        "this endpoint accepts nothing else. THIS IS NOT AN ABSENCE -- "
                        "the award may well exist. Look it up by name or keyword first "
                        "(`evidence-search usaspending \"<recipient>\"`), then pass the "
                        "numeric id from that result's meta.record_id."))
        return verified_absence(
            num, Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"]),
            f"usaspending award detail for numeric award id {num}",
            probes=[Probe(source=SOURCE, endpoint=url, query=num,
                          corpus="USAspending award detail, keyed on its internal numeric id",
                          result_count=0, exact_match_supported=True, at=time.time())],
            caveats=["Rests on an HTTP 404 for this internal numeric id. It says "
                     "nothing about an award known by a PIID or a recipient name."])
    if r.status_code >= 400:
        return AccessBlocker(query=num, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "status"}),
                             mechanism=Blocker.SERVER_ERROR, url=url,
                             detail=(f"HTTP {r.status_code}. Pass the NUMERIC id from a "
                                     "result's meta.record_id -- a constructed "
                                     "CONT_AWD_... string 404s here."))
    d = r.json()
    try:
        store.put_record(f"{SOURCE}:detail:{num}", SOURCE, num, d)
    except Exception:
        pass

    c = d.get("latest_transaction_contract_data") or {}
    rec = d.get("recipient") or {}
    return Hit(query=num, coverage=Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"]),
               results=[Result(
                   url=f"https://www.usaspending.gov/award/{num}",
                   title=f"{(rec.get('recipient_name') or '?')} — {d.get('piid') or num}",
                   snippet=" | ".join((
                       (f"PSC {c['product_or_service_code']}: "
                        f"{c.get('product_or_service_description') or '?'}"
                        if c.get("product_or_service_code")
                        else "PSC: (none in record)"),
                       (f"NAICS {c['naics']}: {c.get('naics_description') or '?'}"
                        if c.get("naics") else "NAICS: (none in record)"),
                       (f"DESC: {d['description']}" if d.get("description")
                        else "DESC: (none in record)"),
                   )),
                   source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
                   meta={"record_id": f"{SOURCE}:detail:{num}", "piid": d.get("piid"),
                         "psc": c.get("product_or_service_code"),
                         "psc_description": c.get("product_or_service_description"),
                         "naics": c.get("naics"), "naics_description": c.get("naics_description"),
                         "description": d.get("description"),
                         "extent_competed": c.get("extent_competed_description"),
                         "solicitation_procedures": c.get("solicitation_procedures_description"),
                         "recipient": rec.get("recipient_name"),
                         "recipient_uei": rec.get("recipient_uei"),
                         "awarding_agency": ((d.get("awarding_agency") or {}).get("toptier_agency") or {}).get("name"),
                         "funding_subtier": ((d.get("funding_agency") or {}).get("subtier_agency") or {}).get("name"),
                         "total_obligation": d.get("total_obligation")},
               )])


def _name_matches(query: str, names: list[str]) -> bool:
    q = query.strip().lower()
    return any(q in nm.lower() or nm.lower() in q for nm in names)


def _entity_caveat(query: str, names: list[str], n_distinct: int) -> str | None:
    if not names:
        return None
    if len(names) == 1 and not _name_matches(query, names):
        return (f"QUERY {query!r} RETURNED RECORDS FOR {names[0]!r} -- the name you searched "
                "is not the name on these awards. Confirm this is the entity you meant "
                "before citing the total.")
    if n_distinct > 1:
        return (f"MATCHED {n_distinct} DISTINCT RECIPIENT NAMES: {', '.join(names)}. "
                "Do NOT add this total to another vendor's sum without checking for shared "
                "award IDs -- that double-count is how a $10.7B figure reached an edited draft.")
    return None


def dollar_sum(query: str, by_recipient: bool = True, award_types: list[str] | None = None,
               date_from: str | None = None, date_to: str | None = None,
               agency: str | None = None,
               max_pages: int = 10, store: Store | None = None,
               limiter: Limiter | None = None):
    """Sum Award Amount across pages."""
    blocked = _solicitation_blocker(query, f"{API}/search/spending_by_award/")
    if blocked:
        return blocked
    store = store or Store()
    limiter = limiter or Limiter(store)
    types = list(award_types or CONTRACT_TYPES)
    inst = _piid_instrument(query)
    if inst in _IDV_INSTRUMENTS and award_types is None:
        types = list(IDV_TYPES)
    total = 0.0
    n = 0
    seen_rows = 0
    unpriced = 0
    page = 1
    more = False
    recipients: set[str] = set()
    while page <= max_pages:
        allowed, retry, why = limiter.reserve(SOURCE)
        if not allowed:
            if retry and retry <= 5 and "min interval" in why:
                time.sleep(retry)
                allowed, retry, why = limiter.reserve(SOURCE)
            if not allowed:
                return RateLimited(
                    query=query,
                    coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                    source=SOURCE, retry_after_s=int(retry) if retry else None,
                    detail=(f"{why} -- summed {n} awards across {page-1} page(s) before "
                            "stopping. This is a PARTIAL sum; do not report it as a total."))
        data, err = _post("/search/spending_by_award/", {
            "filters": _filters(query, by_recipient, types, date_from, date_to, agency=agency),
            "fields": ["Award ID", "Recipient Name", "Award Amount"],
            "limit": 100, "page": page,
            **({"sort": "Award Amount", "order": "desc"}
               if set(types) & set(CONTRACT_TYPES + IDV_TYPES)
               else {"sort": "Award ID", "order": "desc"})})
        limiter.note_outcome(SOURCE, err)
        if err:
            kind, detail = err
            return AccessBlocker(query=query,
                                 coverage=Coverage(queried=[SOURCE], errored={SOURCE: kind}),
                                 mechanism=Blocker.SERVER_ERROR,
                                 url=f"{API}/search/spending_by_award/", detail=detail)
        try:
            rows = json_rows(data, "results", total_key=None)
        except ValueError as e:
            return AccessBlocker(
                query=query,
                coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
                mechanism=Blocker.SERVER_ERROR,
                url=f"{API}/search/spending_by_award/",
                detail=f"payload shape changed ({e}) -- NOT an absence.")
        seen_rows += len(rows)
        for r in rows:
            if not isinstance(r, dict):
                unpriced += 1
                continue
            v = r.get("Award Amount")
            if isinstance(v, (int, float)):
                total += v
                n += 1
            else:
                unpriced += 1
            if r.get("Recipient Name"):
                recipients.add(r["Recipient Name"])
        more = bool((data.get("page_metadata") or {}).get("hasNext"))
        if not more:
            break
        page += 1

    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"])
    if not n:
        if seen_rows:
            return AccessBlocker(
                query=query,
                coverage=Coverage(queried=[SOURCE], errored={SOURCE: "unparseable-amounts"}),
                mechanism=Blocker.SERVER_ERROR,
                url=f"{API}/search/spending_by_award/",
                detail=(f"{seen_rows} award row(s) returned, {unpriced} with an "
                        "unreadable `Award Amount` -- the payload shape may have "
                        "changed. This is NOT an absence: the awards exist."))
        if more:
            return AccessBlocker(
                query=query,
                coverage=Coverage(queried=[SOURCE], errored={SOURCE: "unread-pages"}),
                mechanism=Blocker.SERVER_ERROR,
                url=f"{API}/search/spending_by_award/",
                detail=("page 1 returned no rows but the API reports hasNext -- "
                        "pages went unread, so absence cannot be certified."))
        ag = resolve_agency(agency)
        return verified_absence(
            query, cov, f"usaspending dollar sum ({query})",
            probes=[Probe(source=SOURCE, endpoint=f"{API}/search/spending_by_award/",
                          query=query,
                          params={"types": ",".join(types),
                                  "agency": ag["name"] if ag else ""},
                          corpus="USAspending prime awards",
                          result_count=0, exact_match_supported=False, at=time.time())],
            not_searched=["subawards", "pass-through recipients"],
            caveats=["recipient_search_text is a fuzzy SEARCH, not an entity resolver"]
                    + _reporting_lag_caveat(date_from, date_to))

    names = sorted(recipients)[:6]
    complete = not more
    return Hit(query=query, coverage=cov, results=[Result(
        url=f"https://www.usaspending.gov/search?keywords={query}",
        title=(f"${total:,.2f} across {n} awards" if complete
               else f"AT LEAST ${total:,.2f} across {n}+ awards (page cap hit)"),
        snippet=("complete" if complete else
                 f"FLOOR ONLY: stopped at the {max_pages}-page cap with more available"),
        source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
        meta={"dollar_total": round(total, 2), "awards_summed": n,
              "complete": complete, "pages_read": page,
              "date_from": date_from, "date_to": date_to,
              "scope": ("all time" if not (date_from or date_to)
                        else f"{date_from or 'earliest'} to {date_to or 'latest'}"),
              "recipient_names_matched": names,
              "queried_name_matches_result": _name_matches(query, names),
              "entity_caveat": _entity_caveat(query, names, len(recipients)),
              "caveat": (None if complete else
                         "This is a FLOOR, not a total -- raise --max-pages to close it.")},
    )])


def _parse_awards(data, query: str, page: int, store) -> list[Result]:
    """Rows into Results. `data` is already-decoded JSON from _post_transport."""
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    rows = json_rows(data, "results", total_key=None)
    pm = data.get("page_metadata") or {}
    if isinstance(rows, RecognizedEmpty):
        if isinstance(pm, dict) and pm.get("hasNext"):
            raise Unanswerable(
                f"page {page} returned no rows but the API reports hasNext -- "
                "pages went unread, so absence cannot be certified.")
        if page > 1:
            raise Unanswerable(
                f"page {page} returned no rows: that is the end of the listing, "
                "NOT an absence. Page 1 answers whether any awards match.")
        return rows

    out = []
    for r in rows:
        aid = r.get("Award ID") or ""
        internal = r.get("internal_id")
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
    return out


def search(query: str, by_recipient: bool = True, award_types: list[str] | None = None,
           date_from: str | None = None, date_to: str | None = None,
           agency: str | None = None, limit: int = 25,
           page: int = 1, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """List awards."""
    blocked = _solicitation_blocker(query, f"{API}/search/spending_by_award/")
    if blocked:
        return blocked
    store = store or Store()
    routed: list[str] = []
    inst = _piid_instrument(query)
    if award_types is None and inst in _IDV_INSTRUMENTS:
        types = list(IDV_TYPES)
        routed.append(f"{query!r} is shaped like an IDV PIID (instrument type {inst!r}), "
                      "so the listing routed to `--group idvs` automatically.")
    else:
        types = list(award_types or CONTRACT_TYPES)

    groups = {g for g, codes in AWARD_GROUPS.items() if set(types) & set(codes)}
    if len(groups) > 1:
        return AccessBlocker(
            query=query,
            coverage=Coverage(queried=[SOURCE], errored={SOURCE: "mixed-award-groups"}),
            mechanism=Blocker.SERVER_ERROR, url=f"{API}/search/spending_by_award/",
            detail=("USAspending rejects a listing that mixes award-type groups "
                    f"({', '.join(sorted(groups))}). List one group at a time: "
                    "--group contracts | idvs | assistance. (`--count` accepts a "
                    "mixed list, which is why a total can succeed where a listing "
                    "cannot.)"))

    body = {
        "filters": _filters(query, by_recipient, types, date_from, date_to, agency=agency),
        "fields": FIELDS, "limit": limit, "page": page,
        **({"sort": "Award Amount", "order": "desc"}
           if set(types) & set(CONTRACT_TYPES + IDV_TYPES)
           else {"sort": "Award ID", "order": "desc"})}

    ag = resolve_agency(agency)
    ag_note = f", agency={ag['name']}" if ag else ""
    mode = "recipient" if by_recipient else "keyword"
    return run_source(
        query, source=SOURCE, url=f"{API}/search/spending_by_award/",
        searched=(f"usaspending awards, {mode} search across "
                  f"{len(types)} award types{ag_note}"),
        parse=lambda d: _parse_awards(d, query, page, store),
        transport=_post_transport(body),
        on_blocked=_keep_transport_outcome,
        store=store, limiter=limiter, use_cache=use_cache,
        exact_match_supported=False,
        corpus=("USAspending: federal awards from FY2008. A defined corpus, but "
                "this LISTING is one page -- `--count` gives the real total."),
        caveats=routed + _reporting_lag_caveat(date_from, date_to) + ([] if by_recipient else
                 ["KEYWORD search is full-text across award descriptions: it "
                  "matches records CONTAINING the words, not awards TO a vendor."]),
        not_searched=["awards below the reporting threshold",
                      "classified and otherwise unreported spending",
                      f"award-type groups other than {', '.join(sorted(groups)) or 'contracts'}"],
        cache_params={"recipient": by_recipient, "types": ",".join(types),
                      "date_from": date_from, "date_to": date_to,
                      "agency": (ag["name"] if ag else None),
                      "page": page, "limit": limit},
    )


def idv_orders(piid_or_id: str, limit: int = 25, page: int = 1,
               store: Store | None = None, limiter: Limiter | None = None,
               use_cache: bool = True):
    """Child delivery/task orders issued under an IDV/IDIQ (#17 follow-on).

    Answers: "have any task orders been issued under this IDIQ vehicle yet?"
    Uses `/api/v2/awards/idvs/awards/` when a numeric or `CONT_IDV_...` id is
    passed, or `/api/v2/search/spending_by_award/` with `award_ids` / parent
    PIID search on contract award types A-D.
    """
    raw = (piid_or_id or "").strip().removeprefix(f"{SOURCE}:").strip()
    blocked = _solicitation_blocker(raw, f"{API}/awards/idvs/awards/")
    if blocked:
        return blocked
    store = store or Store()

    url = f"{API}/awards/idvs/awards/"
    body = {
        "award_id": int(raw) if raw.isdigit() else raw,
        "type": "child_awards",
        "limit": limit,
        "page": page,
        "sort": "obligated_amount",
        "order": "desc",
    }

    def _parse_child(data) -> list[Result]:
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        rows = json_rows(data, "results", total_key=None)
        pm = data.get("page_metadata") or {}
        if isinstance(rows, RecognizedEmpty):
            if isinstance(pm, dict) and pm.get("hasNext"):
                raise Unanswerable("page returned no rows but reports hasNext")
            return rows
        out = []
        for r in rows:
            if not isinstance(r, dict):
                raise ValueError("a child award row is not an object")
            aid = r.get("piid") or r.get("Award ID") or ""
            internal = r.get("generated_internal_id") or r.get("internal_id") or aid
            amt = r.get("obligated_amount") if "obligated_amount" in r else r.get("Award Amount")
            rec_name = r.get("recipient_name") or r.get("Recipient Name") or "?"
            ag_name = r.get("awarding_agency") or r.get("Awarding Agency") or ""
            start = r.get("start_date") or r.get("Start Date") or ""
            end = r.get("end_date") or r.get("End Date") or ""
            desc = r.get("description") or r.get("Description") or ""
            rid = f"{SOURCE}:child:{internal}"
            try:
                store.put_record(rid, SOURCE, raw, r)
            except Exception:
                pass
            out.append(Result(
                url=f"https://www.usaspending.gov/award/{internal}",
                title=f"{rec_name} — {aid} (under {raw})",
                snippet=" | ".join(x for x in (
                    f"${amt:,.2f}" if isinstance(amt, (int, float)) else None,
                    ag_name, f"{start}→{end}" if (start or end) else None,
                    desc[:100] if desc else None,
                ) if x),
                source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
                meta={"record_id": rid, "parent_idv": raw, "piid": aid,
                      "recipient": rec_name, "obligated_amount": amt,
                      "agency": ag_name, "start": start, "end": end,
                      "description": desc},
            ))
        return out

    return run_source(
        raw, source=SOURCE, url=url,
        searched=f"usaspending child task orders under IDV {raw}",
        parse=_parse_child,
        transport=_post_transport(body),
        on_blocked=_keep_transport_outcome,
        store=store, limiter=limiter, use_cache=use_cache,
        exact_match_supported=True,
        corpus=f"USAspending child delivery/task orders issued under IDV {raw}.",
        not_searched=[
            "solicitations or task-order RFPs not yet awarded (check SAM.gov)",
            "DoD task orders within the 90-day FPDS reporting lag",
        ],
        caveats=["An IDV with zero child orders has been awarded as a vehicle "
                 "(ceiling only) with no task orders obligated against it yet."],
        cache_params={"mode": "idv_orders", "page": page, "limit": limit},
    )


def transactions(award_id_or_piid: str, limit: int = 25, page: int = 1,
                 store: Store | None = None, limiter: Limiter | None = None,
                 use_cache: bool = True):
    """Per-award transaction / modification history (FEEDBACK.md top open request).

    Answers: "is this a new contract or a modification to an existing one?"
    """
    raw = (award_id_or_piid or "").strip().removeprefix(f"{SOURCE}:").removeprefix("detail:").strip()
    blocked = _solicitation_blocker(raw, f"{API}/search/spending_by_transaction/")
    if blocked:
        return blocked
    store = store or Store()

    url = f"{API}/search/spending_by_transaction/"
    body = {
        "filters": {
            "award_ids": [raw],
            "award_type_codes": ALL_AWARD_TYPES,
        },
        "fields": TRANSACTION_FIELDS,
        "limit": limit,
        "page": page,
        "sort": "Action Date",
        "order": "desc",
    }

    def _parse_tx(data) -> list[Result]:
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
        rows = json_rows(data, "results", total_key=None)
        pm = data.get("page_metadata") or {}
        if isinstance(rows, RecognizedEmpty):
            if isinstance(pm, dict) and pm.get("hasNext"):
                raise Unanswerable("page returned no rows but reports hasNext")
            return rows
        out = []
        for r in rows:
            if not isinstance(r, dict):
                raise ValueError("a transaction row is not an object")
            aid = r.get("Award ID") or raw
            mod = r.get("Modification Number")
            mod_str = f"Mod {mod}" if mod not in (None, "") else "Base/Mod ?"
            dt = r.get("Action Date") or ""
            amt = r.get("Transaction Amount")
            desc = r.get("Transaction Description") or ""
            rec_name = r.get("Recipient Name") or "?"
            internal = r.get("internal_id") or r.get("generated_internal_id") or aid
            tx_id = r.get("Transaction ID") or f"{aid}:{mod}:{dt}"
            rid = f"{SOURCE}:tx:{tx_id}"
            try:
                store.put_record(rid, SOURCE, raw, r)
            except Exception:
                pass
            out.append(Result(
                url=f"https://www.usaspending.gov/award/{internal}",
                title=f"{rec_name} — {aid} ({mod_str} on {dt})",
                snippet=" | ".join(x for x in (
                    f"${amt:,.2f}" if isinstance(amt, (int, float)) else None,
                    r.get("Action Type"),
                    r.get("Awarding Sub Agency") or r.get("Awarding Agency"),
                    desc[:120] if desc else None,
                ) if x),
                source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
                meta={"record_id": rid, "award_id": aid, "modification_number": mod,
                      "action_date": dt, "transaction_amount": amt,
                      "action_type": r.get("Action Type"),
                      "description": desc, "recipient": rec_name,
                      "agency": r.get("Awarding Agency"),
                      "sub_agency": r.get("Awarding Sub Agency"),
                      "has_next_page": bool(pm.get("hasNext"))},
            ))
        return out

    return run_source(
        raw, source=SOURCE, url=url,
        searched=f"usaspending transaction history for award {raw}",
        parse=_parse_tx,
        transport=_post_transport(body),
        on_blocked=_keep_transport_outcome,
        store=store, limiter=limiter, use_cache=use_cache,
        exact_match_supported=True,
        corpus=f"USAspending transaction-level modification history for award {raw}.",
        not_searched=[
            "subaward transactions",
            "modifications within the 90-day DoD FPDS reporting lag",
        ],
        caveats=["Pass the PIID (e.g. 70CDCR26FR0000001) or generated award ID; "
                 "an internal numeric surrogate ID belongs to --detail."],
        cache_params={"mode": "transactions", "page": page, "limit": limit},
    )
