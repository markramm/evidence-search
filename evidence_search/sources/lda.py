"""Senate LDA — lobbying disclosure filings (LD-1/LD-2), keyless.

Probed 2026-09-29 (issue #19, following #7's "re-probe before trusting the
tier" rule — #7 had filed LDA as Tier 5, interactive/blocked). It is not:
`lda.senate.gov` 301-redirects to `https://lda.gov/api/v1/`, which answers
plain, unauthenticated JSON —
`/filings/?client_name=<name>&filing_year=<yyyy>&page_size=50`. No key, no
browser, no session.

The research question this answers: **did a registrant's LD-2 name a specific
bill, in a given year or range** — "no LD-2 naming H.R. N for client X in
years Y-Z" is the negative a draft needs before it can claim a bill was not
lobbied. `client_name` / `registrant_name` are case-insensitive SUBSTRING
matches (verified: `client_name=Purdue` also returns Purdue Research
Foundation and Purdue University), not an exact-entity lookup — so a result's
own `meta.client` / `meta.registrant` must be checked, the same discipline
usaspending's recipient-name caveat already asks for.

`--bill` filters client-side: the API has no bill-text search, so every
filing in scope is fetched and each one's `lobbying_activities[].description`
is scanned for the bill number, spacing and periods normalized ("H.R. 2994",
"H.R.2994", "HR 2994" and "hr2994" all match the same filing).

Regression case (the one that got LDA re-probed): Purdue Pharma LP's 2008 Q1
and Q2 LD-2s name "National Pain Care Policy Act (H.R. 2994)".
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from urllib.parse import urlencode

from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, Probe,
                            RateLimited, Result, replay_cached, verified_absence)
from ..core.store import CachePoisoned, Store, cache_key

SOURCE = "lda"
API = "https://lda.gov/api/v1"

#: Electronic LD-2 filing was mandated by the Honest Leadership and Open
#: Government Act of 2007, building on the LDA's 1999 e-filing requirement.
#: Filings from before this are not in the API at all -- they are the
#: "paper-era" boundary named in every absence below.
COVERAGE_START_YEAR = 1999

#: lda.gov paginates like a standard DRF list view. 5 pages * 50 is enough
#: headroom for any one registrant/client across a multi-year range (Purdue
#: Pharma's busiest year was 4 filings); a query that still has more after
#: that is named as a partial read rather than silently truncated.
DEFAULT_PAGE_SIZE = 50
MAX_PAGES = 5

#: A human-entered bill reference, loosely punctuated: "H.R. 2994", "HR2994",
#: "H.R.2994", "S. 2160", "H.Res. 123", "S.Res. 45". Chamber letters, then an
#: optional trailing period before the number.
_BILL_RE = re.compile(
    r"^\s*(H\.?\s*RES\.?|S\.?\s*RES\.?|H\.?\s*R\.?|S\.?)\s*\.?\s*(\d+)\s*$", re.I)


def _parse_bill(bill: str):
    """('HR'|'S'|'HRES'|'SRES', '2994') from a human string, or None if unrecognised."""
    m = _BILL_RE.match((bill or "").strip())
    if not m:
        return None
    chamber = re.sub(r"[.\s]", "", m.group(1)).upper()
    return chamber, m.group(2)


def _bill_pattern(chamber: str, number: str) -> re.Pattern:
    """A regex that finds `chamber number` in free text regardless of spacing/periods.

    Built letter-by-letter so "H.R. 2994", "H R 2994" and "HR2994" all match
    the same compiled pattern -- LD-2 descriptions use all three in the wild.
    `(?!\\d)` stops "H.R. 2994" from matching inside "H.R. 29945".
    """
    letters = list(chamber)
    parts = []
    for i, ch in enumerate(letters):
        parts.append(re.escape(ch))
        if i < len(letters) - 1:
            parts.append(r"\.?\s*")
    chamber_pat = "".join(parts)
    return re.compile(rf"(?<![A-Za-z0-9]){chamber_pat}\.?\s*{number}(?!\d)", re.I)


def _activity_matches(filing: dict, pattern: re.Pattern) -> list[dict]:
    return [a for a in (filing.get("lobbying_activities") or [])
            if pattern.search(a.get("description") or "")]


def _filing_result(filing: dict, matched: list[dict] | None) -> Result:
    client = (filing.get("client") or {}).get("name") or "?"
    registrant = (filing.get("registrant") or {}).get("name") or "?"
    uuid = filing.get("filing_uuid") or ""
    period = filing.get("filing_period_display") or filing.get("filing_period") or ""
    year = filing.get("filing_year")
    ftype = filing.get("filing_type_display") or filing.get("filing_type") or ""
    issues = sorted({a.get("general_issue_code_display") for a in
                     (filing.get("lobbying_activities") or [])
                     if a.get("general_issue_code_display")})
    if matched:
        snippet = " | ".join((a.get("description") or "")[:200].replace("\n", " ")
                             for a in matched[:2])
    else:
        snippet = ", ".join(issues[:4])
    return Result(
        url=filing.get("filing_document_url") or filing.get("url") or "",
        title=f"{client} — {registrant}: LD-2 {period} {year} ({ftype})",
        snippet=snippet,
        source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
        meta={"record_id": f"{SOURCE}:{uuid}", "filing_uuid": uuid,
              "client": client, "registrant": registrant, "filing_year": year,
              "filing_period": filing.get("filing_period"),
              "filing_type": filing.get("filing_type"),
              "filing_type_display": ftype, "dt_posted": filing.get("dt_posted"),
              "filing_document_url": filing.get("filing_document_url"),
              "issues": issues,
              "matched_activities": [
                  {"description": a.get("description"),
                   "general_issue_code": a.get("general_issue_code_display")}
                  for a in (matched or [])],
              "income": filing.get("income"), "expenses": filing.get("expenses")},
    )


_NOT_SEARCHED = [
    f"LD-2 filings before {COVERAGE_START_YEAR} (lda.gov's electronic coverage start)",
    "LD-203 (semiannual lobbyist/registrant political contribution reports)",
    "paper-era filings never entered into the LDA electronic system",
]

_FUZZY_NAME_CAVEAT = (
    "client_name/registrant_name is a case-insensitive SUBSTRING match, not an "
    "exact-entity lookup -- querying 'Purdue' also returns Purdue Research "
    "Foundation and Purdue University. Check meta.client/meta.registrant on "
    "each result before attributing a filing.")

_AMENDMENT_CAVEAT = (
    "amendment filings (filing_type values like '1A') are not reconciled "
    "against the report they amend -- if a later amendment superseded one of "
    "these filings, that supersession is not flagged here.")


def search(client_name: str | None = None, registrant_name: str | None = None,
           years: list[int] | None = None, bill: str | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True, page_size: int = DEFAULT_PAGE_SIZE,
           max_pages: int = MAX_PAGES):
    """Search LD-2 filings by client and/or registrant and year(s).

    `bill` filters client-side across every filing fetched; omit it to list
    filings themselves. Hand-rolled pagination, in the open style of
    `usaspending.dollar_sum` -- CONTRIBUTING.md's rule for a source that must
    hand-roll its shell: it still goes through `reserve()`/`note_outcome()`
    every page, and every exit is one of the five typed outcomes.
    """
    store = store or Store()
    limiter = limiter or Limiter(store)
    years = sorted(set(years or []))

    pattern = None
    if bill:
        parsed = _parse_bill(bill)
        if parsed is None:
            return AccessBlocker(
                query=bill,
                coverage=Coverage(queried=[SOURCE], errored={SOURCE: "bad-bill-format"}),
                mechanism=Blocker.WRONG_ID_TYPE, url=f"{API}/filings/",
                detail=(f"{bill!r} does not look like a bill number. Expected a "
                        "form like 'H.R. 2994', 'S. 2160', 'H.Res. 123' or "
                        "'S.Res. 45' (spacing and periods are normalized)."))
        pattern = _bill_pattern(*parsed)

    who = ", ".join(x for x in (
        f"client={client_name!r}" if client_name else None,
        f"registrant={registrant_name!r}" if registrant_name else None) if x) or "?"
    years_txt = f"{years[0]}-{years[-1]}" if years else "all years"
    query = f"{who} years={years_txt}" + (f" bill={bill!r}" if bill else "")

    key = cache_key(SOURCE, query, bill=bill or "", page_size=page_size)
    if use_cache:
        entry = store.get_entry(key, with_meta=True)
        if entry is not None:
            return replay_cached(query, entry[0], entry[1], source=SOURCE,
                                 searched=f"LDA filings ({query})", meta=entry[2])

    params = []
    if client_name:
        params.append(("client_name", client_name))
    if registrant_name:
        params.append(("registrant_name", registrant_name))
    for y in years:
        params.append(("filing_year", str(y)))
    params.append(("page_size", str(page_size)))
    first_url = f"{API}/filings/?{urlencode(params)}"

    from ..core.http import fetch as http_fetch  # local: re-imports per call,
    # so a test's monkeypatch on evidence_search.core.http.fetch takes effect
    # (the same discipline run_source itself uses).

    filings: list[dict] = []
    pages_read = 0
    more = False
    url = first_url
    t0 = time.time()
    while url and pages_read < max_pages:
        allowed, retry, why = limiter.reserve(SOURCE)
        if not allowed:
            # Paging is one logical operation; absorb a SHORT spacing wait
            # rather than returning a half-read set that looks complete.
            if retry and retry <= 5 and "min interval" in why:
                time.sleep(retry)
                allowed, retry, why = limiter.reserve(SOURCE)
            if not allowed:
                return RateLimited(
                    query=query, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                    source=SOURCE, retry_after_s=int(retry) if retry else None,
                    detail=(f"{why} -- read {pages_read} page(s) before stopping. "
                            "This is a PARTIAL read; do not report an absence from it."))

        body, blocked = http_fetch(url, source=SOURCE, query=query)
        limiter.note_outcome(SOURCE, blocked)
        if blocked:
            blocked.coverage = Coverage(queried=[SOURCE], errored={SOURCE: "blocked"},
                                        elapsed_ms=int((time.time() - t0) * 1000))
            return blocked

        try:
            data = json.loads(body)
        except (json.JSONDecodeError, TypeError) as e:
            return AccessBlocker(
                query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
                mechanism=Blocker.SERVER_ERROR, url=url,
                detail=f"payload shape changed ({e}) -- the source may have altered its format")
        if not isinstance(data, dict) or "results" not in data:
            return AccessBlocker(
                query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
                mechanism=Blocker.SERVER_ERROR, url=url,
                detail=f"expected a paginated JSON object, got {type(data).__name__}")

        from ..core.archive import archive as _archive
        try:
            # Keyed by the cache key (this exact query) and page number, so two
            # different searches' raw pages never collide under one filename --
            # archive() overwrites same-named content rather than refusing, so
            # a collision would silently lose the earlier query's provenance.
            _archive(body.encode() if isinstance(body, str) else body,
                    f"lda_filings_{key}_p{pages_read}.json", url, "evidence-search:lda")
        except Exception:
            pass  # archiving must never fail the search itself

        filings.extend(data.get("results") or [])
        pages_read += 1
        nxt = data.get("next")
        more = bool(nxt) and pages_read >= max_pages
        url = nxt if nxt and pages_read < max_pages else None

    for f in filings:
        try:
            store.put_record(f"{SOURCE}:{f.get('filing_uuid')}", SOURCE, query, f)
        except Exception:
            pass

    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"],
                   elapsed_ms=int((time.time() - t0) * 1000))

    not_searched = list(_NOT_SEARCHED)
    caveats = [_FUZZY_NAME_CAVEAT, _AMENDMENT_CAVEAT]
    if more:
        caveats.append(
            f"stopped at the {max_pages}-page cap ({pages_read * page_size} filings "
            "read) with more available upstream -- this is a PARTIAL read, not a "
            "complete one; narrow by registrant or year to close it")

    params_for_probe = {"client_name": client_name or "", "registrant_name": registrant_name or "",
                        "years": ",".join(map(str, years)) or "all", "bill": bill or ""}

    if bill:
        scored = [(f, _activity_matches(f, pattern)) for f in filings]
        hits = [(f, m) for f, m in scored if m]
        probes = [Probe(source=SOURCE, endpoint=f"{API}/filings/", query=query,
                        params=params_for_probe,
                        corpus="Senate LDA LD-2 filings (lda.gov/api/v1/filings/), "
                               "lobbying_activities descriptions scanned client-side "
                               "for the bill number",
                        result_count=len(hits), exact_match_supported=False, at=time.time())]
        if not hits:
            meta = {"probes": [asdict(p) for p in probes],
                    "not_searched": not_searched, "caveats": caveats}
            if use_cache:
                try:
                    store.put(key, SOURCE, [], ttl_s=86400, meta=meta)
                except CachePoisoned:
                    pass
            return verified_absence(
                query, cov, f"no LD-2 naming {bill} for {who} in {years_txt}",
                probes=probes, not_searched=not_searched, caveats=caveats)
        results = [_filing_result(f, m) for f, m in hits]
    else:
        probes = [Probe(source=SOURCE, endpoint=f"{API}/filings/", query=query,
                        params=params_for_probe,
                        corpus="Senate LDA LD-2 filings (lda.gov/api/v1/filings/)",
                        result_count=len(filings), exact_match_supported=False, at=time.time())]
        if not filings:
            meta = {"probes": [asdict(p) for p in probes],
                    "not_searched": not_searched, "caveats": caveats}
            if use_cache:
                try:
                    store.put(key, SOURCE, [], ttl_s=86400, meta=meta)
                except CachePoisoned:
                    pass
            return verified_absence(
                query, cov, f"no LD-2 filings for {who} in {years_txt}",
                probes=probes, not_searched=not_searched, caveats=caveats)
        results = [_filing_result(f, None) for f in filings]

    # Caveats (fuzzy name match, the amendment caveat, and -- when it applies
    # -- the page-cap partial-read warning) must survive onto a Hit too, not
    # only an absence. Attached per result rather than dropped once filings
    # were found: a Hit built from a partial read is still a partial read.
    for r in results:
        r.meta["search_caveats"] = list(caveats)

    if use_cache:
        try:
            store.put(key, SOURCE, [r.__dict__ for r in results], ttl_s=86400)
        except CachePoisoned as e:
            cov.cache_write_refused = str(e)

    return Hit(query=query, coverage=cov, results=results)
