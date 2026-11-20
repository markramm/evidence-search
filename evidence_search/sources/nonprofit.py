"""ProPublica Nonprofit Explorer — IRS Form 990 search and EIN filings (#4).

Verified live 2026-08-19 and 2026-09-29:
  * Organization search (keyless JSON):
    `https://projects.propublica.org/nonprofits/api/v2/search.json?q=<name>`
  * Organization & filing history by EIN (keyless JSON):
    `https://projects.propublica.org/nonprofits/api/v2/organizations/<ein>.json`

Why ProPublica rather than the IRS directly: the IRS Tax-Exempt Organization
Search (`apps.irs.gov/app/eos/`) returns HTTP 403 Access Denied to automated
clients. ProPublica's v2 API wraps the same Form 990/990-EZ/990-PF filings and
is open.

**Entity-collision trap (verified 2026-09-29 on live reporting):**
  * `q=DonorsTrust` (one word) returns `total_results: 1`, "Donorsrus Inc",
    Jupiter FL -- a completely different organization.
  * `q="Donors Trust"` returns 2 results and ranks "Donors Trust, Omaha NE
    (EIN 26-2515785)" first, ahead of "Donors Trust Inc, Alexandria VA
    (EIN 52-2166327)".

So whenever a search returns multiple distinct EINs (or a single organization
whose name does not match the query), the client attaches a loud `entity_caveat`
listing every EIN and state -- the same double-count and misattribution defense
`usaspending` and `fec` enforce.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, urlencode

from ..core.limits import Limiter
from ..core.results import AccessBlocker, Blocker, Coverage, Result
from ..core.source import RecognizedEmpty, Unanswerable, run_source
from ..core.store import Store

SOURCE = "nonprofit"
API = "https://projects.propublica.org/nonprofits/api/v2"

_EIN_RE = re.compile(r"^\s*(\d{2})-?(\d{7})\s*$")


def parse_ein(val: str) -> str | None:
    """Normalize '52-2166327' or '522166327' to '522166327', or None."""
    m = _EIN_RE.match(val or "")
    return f"{m.group(1)}{m.group(2)}" if m else None


def format_ein(ein: str | int | None) -> str:
    s = re.sub(r"\D", "", str(ein or ""))
    return f"{s[:2]}-{s[2:]}" if len(s) == 9 else str(ein or "")


def _name_matches(query: str, org_name: str) -> bool:
    q = re.sub(r"[^a-z0-9]+", " ", query.lower()).strip()
    n = re.sub(r"[^a-z0-9]+", " ", org_name.lower()).strip()
    return bool(q and n and (q in n or n in q))


def _collision_caveat(query: str, orgs: list[dict]) -> str | None:
    distinct: list[str] = []
    seen_eins: set[str] = set()
    names: list[str] = []
    for o in orgs:
        ein = format_ein(o.get("ein") or o.get("strein"))
        nm = (o.get("name") or o.get("sub_name") or "?").strip()
        st = (o.get("state") or "").strip()
        city = (o.get("city") or "").strip()
        if ein and ein not in seen_eins:
            seen_eins.add(ein)
            loc = ", ".join(x for x in (city, st) if x)
            distinct.append(f"{nm} ({loc or '?'}, EIN {ein})")
            names.append(nm)
    if len(distinct) > 1:
        return (f"MATCHED {len(distinct)} DISTINCT EINs: {'; '.join(distinct[:6])}. "
                "Nonprofit names collide across chapters, affiliates and unrelated entities "
                "(verified: 'Donors Trust' ranks Omaha NE 26-2515785 above Alexandria VA "
                "52-2166327). Confirm the EIN and state with `--ein <EIN>` before attributing "
                "revenue or grants.")
    if len(distinct) == 1 and not _name_matches(query, names[0]):
        return (f"QUERY {query!r} RETURNED {distinct[0]} -- the organization name does not "
                "contain your query string (verified trap: 'DonorsTrust' returns 'Donorsrus Inc'). "
                "Confirm this is the entity you meant before citing it.")
    return None


def _parse_search(body: str, query: str, page: int, store: Store | None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"non-JSON response: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    if "organizations" not in data and "total_results" not in data:
        raise ValueError(
            "no `organizations` or `total_results` in ProPublica Nonprofits response "
            f"(keys: {', '.join(sorted(map(str, data))) or 'none'})")

    orgs = data.get("organizations")
    total = data.get("total_results")
    if orgs is None and total == 0:
        return RecognizedEmpty("organizations: [], total_results: 0")
    if not isinstance(orgs, list):
        raise ValueError(f"`organizations` is {type(orgs).__name__}, not a list")
    if not orgs:
        if isinstance(total, bool) or (total is not None and not isinstance(total, int)):
            raise ValueError(f"unreadable `total_results`: {total!r}")
        if isinstance(total, int) and total > 0:
            raise Unanswerable(
                f"ProPublica Nonprofits reports total_results={total} but page {page} "
                "carries no rows -- a page past the end, NOT an absence.")
        return RecognizedEmpty("organizations: []" + (f", total_results: {total}"
                                                      if total is not None else ""))

    if any(not isinstance(o, dict) for o in orgs):
        raise ValueError("an organization row is not an object")

    caveat = _collision_caveat(query, orgs)
    out: list[Result] = []
    for o in orgs:
        raw_ein = str(o.get("ein") or "").strip()
        ein_fmt = o.get("strein") or format_ein(raw_ein)
        name = (o.get("name") or "?").strip()
        sub_name = (o.get("sub_name") or "").strip()
        city = (o.get("city") or "").strip()
        state = (o.get("state") or "").strip()
        ntee = (o.get("ntee_code") or "").strip()
        subsec = o.get("subseccd")
        rid = f"{SOURCE}:{raw_ein or ein_fmt}"
        if store is not None:
            try:
                store.put_record(rid, SOURCE, query, o)
            except Exception:
                pass
        loc = ", ".join(x for x in (city, state) if x)
        snip_bits = [b for b in (
            f"EIN {ein_fmt}" if ein_fmt else "",
            loc,
            f"501(c)({subsec})" if subsec else "",
            f"NTEE {ntee}" if ntee else "",
            f"DBA/sub: {sub_name}" if sub_name and sub_name != name else "",
        ) if b]
        out.append(Result(
            url=f"https://projects.propublica.org/nonprofits/organizations/{raw_ein or ein_fmt}",
            title=f"{name} ({ein_fmt})" + (f" — {loc}" if loc else ""),
            snippet=" | ".join(snip_bits),
            source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
            meta={
                "record_id": rid,
                "ein": ein_fmt,
                "ein_numeric": raw_ein,
                "name": name,
                "sub_name": sub_name or None,
                "city": city or None,
                "state": state or None,
                "ntee_code": ntee or None,
                "subsection_code": subsec,
                "total_matches": total,
                "entity_caveat": caveat,
            },
        ))
    return out


_NOT_SEARCHED = [
    "churches and religious organizations exempt from filing Form 990",
    "small tax-exempt organizations below the gross-receipts threshold filing Form 990-N (e-Postcard) only",
    "Schedule B major-donor identities redacted from public Form 990 disclosures",
    "recent filings not yet processed by the IRS into ProPublica's index",
]


def search(query: str, *, state: str | None = None, page: int = 1,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search nonprofits by name (or auto-route an EIN-shaped query to `by_ein`)."""
    store = store or Store()
    if parse_ein(query) and not state and page == 1:
        return by_ein(query, store=store, limiter=limiter, use_cache=use_cache)

    params = [("q", query)]
    if state:
        params.append(("state[id]", state.strip().upper()))
    if page > 1:
        params.append(("page", str(page - 1)))
    url = f"{API}/search.json?{urlencode(params)}"

    return run_source(
        query, source=SOURCE, url=url,
        searched="propublica nonprofit explorer (IRS Form 990 index)",
        parse=lambda b: _parse_search(b, query, page, store),
        store=store, limiter=limiter, use_cache=use_cache,
        exact_match_supported=False,
        corpus="ProPublica Nonprofit Explorer v2: IRS Form 990, 990-EZ, and 990-PF filings.",
        not_searched=list(_NOT_SEARCHED) + ([f"organizations registered outside state={state.upper()}"] if state else []),
        caveats=[
            "Name search is fuzzy/stemmed: 'DonorsTrust' (one word) returns 'Donorsrus Inc', "
            "and 'Donors Trust' matches multiple distinct EINs. Try spacing/punctuation "
            "variants before writing up an absence, and verify by EIN (`--ein`)."
        ],
        cache_params={"state": (state or "").upper(), "page": page},
    )


def by_ein(ein: str, *, year: int | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Fetch organization detail and Form 990 filing history by EIN (#4)."""
    store = store or Store()
    norm = parse_ein(ein)
    if not norm:
        return AccessBlocker(
            query=ein,
            coverage=Coverage(queried=[SOURCE], errored={SOURCE: "bad-ein"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url=f"{API}/organizations/",
            detail=(f"{ein!r} is not a 9-digit EIN (expected XX-XXXXXXX or 9 digits). "
                    "THIS IS NOT AN ABSENCE -- search by organization name first."))

    fmt = format_ein(norm)
    url = f"{API}/organizations/{norm}.json"

    def _parse_org(body: str) -> list[Result]:
        try:
            d = json.loads(body)
        except json.JSONDecodeError as e:
            raise ValueError(f"non-JSON response: {e}") from e
        if not isinstance(d, dict) or "organization" not in d:
            raise ValueError(
                "no `organization` key in ProPublica EIN response "
                f"(keys: {', '.join(sorted(map(str, d))) or 'none'})")
        org = d.get("organization")
        if not isinstance(org, dict):
            raise ValueError("`organization` is not an object")

        name = (org.get("name") or fmt).strip()
        city = (org.get("city") or "").strip()
        state = (org.get("state") or "").strip()
        ntee = (org.get("ntee_code") or "").strip()
        filings = list(d.get("filings_with_data") or []) + list(d.get("filings_without_data") or [])
        if year is not None:
            filings = [f for f in filings if isinstance(f, dict) and f.get("tax_prd_yr") == year]
            if not filings:
                return RecognizedEmpty(f"organization {fmt} exists; 0 filings for tax_prd_yr={year}")

        rid = f"{SOURCE}:org:{norm}"
        try:
            store.put_record(rid, SOURCE, fmt, d)
        except Exception:
            pass

        if not filings:
            return [Result(
                url=f"https://projects.propublica.org/nonprofits/organizations/{norm}",
                title=f"{name} (EIN {fmt}) — no electronic 990 filings on record",
                snippet=" | ".join(x for x in (
                    f"{city}, {state}".strip(", "),
                    f"NTEE {ntee}" if ntee else "",
                    "0 filings in filings_with_data/filings_without_data",
                ) if x),
                source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
                meta={"record_id": rid, "ein": fmt, "name": name, "city": city,
                      "state": state, "ntee_code": ntee, "filing_years": [],
                      "filings_count": 0},
            )]

        years = sorted({f.get("tax_prd_yr") for f in filings if isinstance(f, dict) and f.get("tax_prd_yr")}, reverse=True)
        out: list[Result] = []
        for f in filings:
            if not isinstance(f, dict):
                continue
            yr = f.get("tax_prd_yr")
            form_map = {0: "990", 1: "990-EZ", 2: "990-PF"}
            form = form_map.get(f.get("formtype"), str(f.get("formtype_str") or "990"))
            rev = f.get("totrevenue")
            exp = f.get("totfuncexpns")
            assets = f.get("totassetsend")
            pdf_url = f.get("pdf_url") or ""
            xml_url = f.get("xml_url") or ""
            out.append(Result(
                url=pdf_url or f"https://projects.propublica.org/nonprofits/organizations/{norm}",
                title=f"{name} (EIN {fmt}) — FY{yr} Form {form}",
                snippet=" | ".join(x for x in (
                    f"rev ${rev:,.0f}" if isinstance(rev, (int, float)) else None,
                    f"exp ${exp:,.0f}" if isinstance(exp, (int, float)) else None,
                    f"assets ${assets:,.0f}" if isinstance(assets, (int, float)) else None,
                    f"{city}, {state}".strip(", "),
                    f"PDF: {pdf_url}" if pdf_url else None,
                ) if x),
                source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
                meta={
                    "record_id": rid,
                    "ein": fmt,
                    "name": name,
                    "city": city or None,
                    "state": state or None,
                    "ntee_code": ntee or None,
                    "tax_year": yr,
                    "form_type": form,
                    "total_revenue": rev,
                    "total_expenses": exp,
                    "total_assets": assets,
                    "pdf_url": pdf_url or None,
                    "xml_url": xml_url or None,
                    "all_filing_years": years,
                },
            ))
        return out

    return run_source(
        fmt + (f" year={year}" if year else ""),
        source=SOURCE, url=url,
        searched=f"propublica nonprofit EIN {fmt}" + (f" (tax year {year})" if year else ""),
        parse=_parse_org,
        store=store, limiter=limiter, use_cache=use_cache,
        answer_on={Blocker.NOT_FOUND: (
            f"Rests on an HTTP 404 for EIN {fmt} on ProPublica Nonprofit Explorer.")},
        exact_match_supported=True,
        corpus=f"ProPublica Nonprofit Explorer organization record and Form 990 filings for EIN {fmt}.",
        not_searched=_NOT_SEARCHED,
        cache_params={"mode": "ein", "ein": norm, "year": year},
    )
