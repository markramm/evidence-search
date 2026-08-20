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

import json
import time
from urllib.parse import quote
from typing import Any

from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, RateLimited,
                            Result, replay_cached, verified_absence)
from ..core.source import run_source
from ..core.store import Store, cache_key

SOURCE = "usaspending"
API = "https://api.usaspending.gov/api/v2"

#: USAspending requires award_type_codes to come from ONE GROUP -- mixing them
#: returns HTTP 422 ("must only contain types from one group"). The COUNT
#: endpoint is laxer and accepts a mixed list, which is how a combined
#: ALL_AWARD_TYPES shipped and then failed only on the LISTING path: --count
#: worked, --limit 422'd.
#:
#: These six groups are read from the API's own 422 response rather than
#: guessed -- a first correction split them into three and was still wrong,
#: because loans, grants, direct payments and other-financial-assistance are
#: four separate groups, not one "assistance" bucket.
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

FIELDS = ["Award ID", "Recipient Name", "Awarding Agency", "Awarding Sub Agency",
          "Award Amount", "Total Outlays", "Start Date", "End Date",
          "Description", "Contract Award Type", "recipient_id"]


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
        return None, ("rate", r.headers.get("Retry-After", "60"))
    if r.status_code >= 400:
        return None, ("status", f"HTTP {r.status_code}: {r.text[:200]}")
    try:
        return r.json(), None
    except json.JSONDecodeError as e:
        return None, ("parse", str(e))


def _post_transport(body: dict):
    """Adapt _post to the (payload, outcome_or_None) shape run_source expects.

    The shell is GET-only because every other source is. Everything around the
    request -- cache, reserve, backoff, absence, CachePoisoned -- is identical
    here, so only the request is swapped rather than the whole shell being
    re-implemented around it, which is what this module used to do.

    _post already classifies its own failures, so the mapping is direct: a
    timeout or a 429 is a RateLimited, anything else is a blocker. Returning
    the decoded dict as the payload means parse callbacks receive JSON rather
    than re-parsing text.
    """
    def transport(url, *, source, query="", headers=None):
        path = url[len(API):] if url.startswith(API) else url
        data, err = _post(path, body)
        if err is None:
            return data, None
        kind, detail = err
        if kind in ("timeout", "rate"):
            return None, RateLimited(
                query=query, coverage=Coverage(queried=[source], errored={source: kind}),
                source=source, retry_after_s=60, detail=detail)
        return None, AccessBlocker(
            query=query, coverage=Coverage(queried=[source], errored={source: kind}),
            mechanism=Blocker.SERVER_ERROR, url=url, detail=detail)
    return transport


def _keep_transport_outcome(blocked, body):
    """Return the transport's own outcome unchanged.

    run_source otherwise rewrites coverage to errored={source: "blocked"},
    which would flatten the kind _post worked out -- "rate" and "timeout" are
    the ones that distinguish a retryable pause from a wall, and a RateLimited
    that reads as blocked loses its retry_after_s meaning.
    """
    return blocked


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


def _parse_counts(data, query: str, by_recipient: bool) -> list[Result]:
    """The count endpoint returns one summary row, not a list of awards."""
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    res = data.get("results") or {}
    total = sum(v for v in res.values() if isinstance(v, int))
    if not total:
        return []

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
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Real totals by award type. The countable primitive this beat needs."""
    types = award_types or ALL_AWARD_TYPES
    mode = "recipient-name" if by_recipient else "keyword (FUZZY)"
    return run_source(
        query, source=SOURCE, url=f"{API}/search/spending_by_award_count/",
        searched=f"usaspending award counts ({mode})",
        parse=lambda d: _parse_counts(d, query, by_recipient),
        transport=_post_transport(
            {"filters": _filters(query, by_recipient, types, date_from, date_to)}),
        on_blocked=_keep_transport_outcome,
        store=store, limiter=limiter, use_cache=use_cache,
        exact_match_supported=False,
        corpus=("USAspending award counts from FY2008. Unlike a web tier this "
                "IS a measurement -- the endpoint counts the matching awards."),
        caveats=([] if by_recipient else
                 ["KEYWORD counts records CONTAINING the words, not awards TO a "
                  "vendor. A count from a keyword search is not a count of "
                  "anything in particular."]),
        not_searched=["awards below the reporting threshold",
                      "classified and otherwise unreported spending"],
        cache_params={"mode": "count", "recipient": by_recipient,
                      "types": ",".join(types),
                      "date_from": date_from, "date_to": date_to},
    )


def detail(record_id: str, store: Store | None = None, limiter: Limiter | None = None):
    """Full FPDS detail for one award: PSC, NAICS, the contracting officer's own words.

    THREE separate workers independently reimplemented this against the raw API
    before it existed, because neither `--group` nor `record` surfaces PSC or
    NAICS -- both return the search summary, and the codes live only on the
    award-detail endpoint.

    Those codes are the good evidence on this beat precisely because they are the
    BUYER's classification, not the vendor's marketing: an ICE award buying a
    "REALISTIC DE-ESCALATION INSTRUCTOR COURSE" coded U013
    "EDUCATION/TRAINING-COMBAT" states the purpose/effect gap inside one record.

    Accepts a bare numeric id or the `usaspending:<id>` record_id from a result.
    """
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
    # A 404 is an ANSWER here, not a transport failure -- either the wrong id
    # type or a genuine absence -- so it must not push the source toward
    # backoff. Only 5xx/transport trouble counts against it.
    if r.status_code >= 500:
        limiter.note_outcome(SOURCE, ("status", f"HTTP {r.status_code}"))
    elif r.status_code < 400:
        limiter.note_outcome(SOURCE, None)
    if r.status_code == 404:
        # A 404 here is AMBIGUOUS and must never become a publishable negative.
        # This endpoint keys on USAspending's INTERNAL numeric award id (the one
        # in a result's meta.record_id / the /award/<n>/ URL), not on a PIID.
        # Passing a PIID -- `70CDCR26FR0000001`, the identifier a human actually
        # has -- 404s even though the award plainly exists and a recipient search
        # returns it. Reporting that as VerifiedAbsence tells a researcher the
        # record is not there, which is the single worst thing this tool can say.
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
        return verified_absence(num, Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"]),
                                f"usaspending award detail for numeric award id {num}")
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
                   # Absent fields are NAMED, never omitted.
                   #
                   # Dropping the line when a code is missing makes silence
                   # ambiguous exactly where the codes are the load-bearing
                   # evidence: a worker comparing two awards saw one PSC line
                   # and one none, and could not tell whether the award has no
                   # PSC or whether --detail failed to print it. Those mean
                   # different things -- one is a finding, the other is a bug --
                   # and they nearly wrote "no PSC assigned" on the strength of
                   # a blank. An explicit "(none in record)" is a fact; a
                   # missing line is a question.
                   snippet=" | ".join((
                       (f"PSC {c['product_or_service_code']}: "
                        f"{c.get('product_or_service_description') or '?'}"
                        if c.get("product_or_service_code")
                        else "PSC: (none in record)"),
                       (f"NAICS {c['naics']}: {c.get('naics_description') or '?'}"
                        if c.get("naics") else "NAICS: (none in record)"),
                       # The award's own words. Already fetched and stored; a
                       # worker had to drop to raw curl for it because it was
                       # in meta but never rendered.
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
    """Warn when the recipient search did not resolve to the entity asked for.

    `recipient_search_text` is a SEARCH, not an entity resolver, and two failure
    modes both produced wrong published figures:

    1. ONE name that is not yours -- querying "Constellis" returns records for
       TRIPLE CANOPY INC. The name searched is not the name on the awards.
    2. SEVERAL names -- a parent-company query sweeps in subsidiary records, so
       two --sum calls that each look authoritative silently count the same
       contracts twice. Verified: "Constellis" and "Triple Canopy" share 55 of
       their top 100 PIIDs, and summing the two yields $10.73B -- the exact wrong
       figure found sitting in a draft with an editor.
    """
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
               max_pages: int = 10, store: Store | None = None,
               limiter: Limiter | None = None):
    """Sum Award Amount across pages. Workers hand-scraped every total before this.

    Reports whether it is COMPLETE or a floor: the API pages, and a vendor with
    more awards than max_pages*100 yields an understatement. A floor reported as
    a total is the confident-wrong-number failure this package keeps hitting.

    DATE BOUNDS ARE HONOURED. They were not, originally: this function took no
    date parameters while the CLI accepted --from/--to, so a bounded query
    silently returned the ALL-TIME total and labelled it "complete". A worker
    caught it by cross-checking against --count, which respected the same bounds.
    A wrong total that says complete is worse than no total.
    """
    store = store or Store()
    limiter = limiter or Limiter(store)
    types = award_types or CONTRACT_TYPES
    total = 0.0
    n = 0
    page = 1
    more = False
    recipients: set[str] = set()
    while page <= max_pages:
        allowed, retry, why = limiter.reserve(SOURCE)
        if not allowed:
            # Paging is one logical operation, so a spacing wait between pages
            # should be absorbed rather than abandoning a half-summed total --
            # a partial sum returned as a result is exactly the floor-as-total
            # error. Wait out SHORT spacing; a real budget window still stops us.
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
            "filters": _filters(query, by_recipient, types, date_from, date_to),
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
        rows = data.get("results") or []
        for r in rows:
            v = r.get("Award Amount")
            if isinstance(v, (int, float)):
                total += v
                n += 1
            if r.get("Recipient Name"):
                recipients.add(r["Recipient Name"])
        more = bool((data.get("page_metadata") or {}).get("hasNext"))
        if not more:
            break
        page += 1

    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"])
    if not n:
        return verified_absence(query, cov, f"usaspending dollar sum ({query})")

    # recipient_search_text is a SEARCH, not an entity resolver. A parent-company
    # name matches subsidiaries' records, so two --sum calls that each look
    # authoritative can silently count the SAME contracts twice. Verified: a
    # "Constellis" sum and a "Triple Canopy" sum share 55 of their top 100 PIIDs
    # and the recipient name returned under "Constellis" is TRIPLE CANOPY INC.
    # Summing the two produced $10.73B -- the exact wrong figure that had been
    # sitting in a piece with an editor.
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
    rows = data.get("results") or []
    pm = data.get("page_metadata") or {}

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
    return out


def search(query: str, by_recipient: bool = True, award_types: list[str] | None = None,
           date_from: str | None = None, date_to: str | None = None, limit: int = 25,
           page: int = 1, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """List awards. Carries the real total from the count endpoint alongside."""
    store = store or Store()
    types = award_types or CONTRACT_TYPES

    # Refuse a mixed group BEFORE spending a ledger slot: the API 422s on it,
    # and this is a caller error we can name precisely rather than a wall.
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
        "filters": _filters(query, by_recipient, types, date_from, date_to),
        "fields": FIELDS, "limit": limit, "page": page,
        # Loans have no "Award Amount" field -- sorting on it returns HTTP 400
        # ("not found in Loan Award mappings"). Sort by the one field every
        # group shares, and let the caller re-sort if they care.
        **({"sort": "Award Amount", "order": "desc"}
           if set(types) & set(CONTRACT_TYPES + IDV_TYPES)
           else {"sort": "Award ID", "order": "desc"})}

    mode = "recipient" if by_recipient else "keyword"
    return run_source(
        query, source=SOURCE, url=f"{API}/search/spending_by_award/",
        searched=(f"usaspending awards, {mode} search across "
                  f"{len(types)} award types"),
        parse=lambda d: _parse_awards(d, query, page, store),
        transport=_post_transport(body),
        on_blocked=_keep_transport_outcome,
        store=store, limiter=limiter, use_cache=use_cache,
        # recipient_search_text is precise on contracts and much fuzzier on
        # assistance awards; keywords is full-text and fuzzy by construction.
        exact_match_supported=False,
        corpus=("USAspending: federal awards from FY2008. A defined corpus, but "
                "this LISTING is one page -- `--count` gives the real total."),
        caveats=([] if by_recipient else
                 ["KEYWORD search is full-text across award descriptions: it "
                  "matches records CONTAINING the words, not awards TO a vendor."]),
        not_searched=["awards below the reporting threshold",
                      "classified and otherwise unreported spending",
                      f"award-type groups other than {', '.join(sorted(groups)) or 'contracts'}"],
        cache_params={"recipient": by_recipient, "types": ",".join(types),
                      "date_from": date_from, "date_to": date_to,
                      "page": page, "limit": limit},
    )
