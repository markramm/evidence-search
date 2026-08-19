"""OSCN — Oklahoma State Courts Network.

Reverse-engineered 2026-08-19. The docket search is an UNAUTHENTICATED GET form:
    https://www.oscn.net/dockets/Results.aspx?db=<county>&lname=X&FiledDateL=...
County codes are lowercase county names ('caddo'). Case pages:
    https://www.oscn.net/dockets/GetCaseInformation.aspx?db=caddo&number=CF-2013-00038

Cloudflare Turnstile engages after roughly 10 fetches per session, so the policy
in limits.py caps us at 8 with a 2s spacing.

This source found: Andrea Frazier's trafficking charge, her $250,000 bond, her
indigent status, and Pamela CROTTY-White's full name of record -- none of which
were in the federal docket.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..core.limits import Limiter
from ..core.results import Result
from ..core.source import run_source
from ..core.store import Store

BASE = "https://www.oscn.net/dockets"
SOURCE = "oscn"

#: Valid `db=` values: Oklahoma's 77 counties, lowercased, plus the appellate
#: databases. A worker had to GUESS `oklahoma` from the `caddo` example in the
#: docs, with no way to tell in advance whether the right token was `oklahoma`,
#: `oklahoma-county`, or a FIPS code -- and a wrong county produces a result
#: that reads like an absence rather than an error. Encoded so the client can
#: reject a bad value instead of silently searching nothing.
COUNTIES = {
    "adair", "alfalfa", "atoka", "beaver", "beckham", "blaine", "bryan",
    "caddo", "canadian", "carter", "cherokee", "choctaw", "cimarron",
    "cleveland", "coal", "comanche", "cotton", "craig", "creek", "custer",
    "delaware", "dewey", "ellis", "garfield", "garvin", "grady", "grant",
    "greer", "harmon", "harper", "haskell", "hughes", "jackson", "jefferson",
    "johnston", "kay", "kingfisher", "kiowa", "latimer", "leflore", "lincoln",
    "logan", "love", "major", "marshall", "mayes", "mcclain", "mccurtain",
    "mcintosh", "murray", "muskogee", "noble", "nowata", "okfuskee",
    "oklahoma", "okmulgee", "osage", "ottawa", "pawnee", "payne",
    "pittsburg", "pontotoc", "pottawatomie", "pushmataha", "rogermills",
    "rogers", "seminole", "sequoyah", "stephens", "texas", "tillman", "tulsa",
    "wagoner", "washington", "washita", "woods", "woodward",
}
#: Appellate and statewide databases, which take the same `db=` parameter.
APPELLATE = {"appellate", "oksc", "okca", "okcr", "all"}
VALID_DBS = COUNTIES | APPELLATE
CASE_RE = re.compile(r"\b([A-Z]{2,3}-\d{4}-\d{5})\b")


def _no_records(html: str) -> bool:
    return "Found No Records" in html


def _parse_results(html: str, county: str) -> list[Result]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[Result] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        if "GetCaseInformation" not in a["href"]:
            continue
        row = a.find_parent("tr")
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")] if row else []
        cells = [c for c in cells if c]
        m = CASE_RE.search(" ".join(cells) or a.get_text(strip=True))
        number = m.group(1) if m else a.get_text(strip=True)
        url = f"{BASE}/{a['href'].lstrip('/')}" if not a["href"].startswith("http") else a["href"]
        if url in seen:
            continue
        seen.add(url)
        out.append(Result(
            url=url, title=" | ".join(cells[:3]) if cells else number,
            snippet=" | ".join(cells), source=f"{SOURCE}:{county}",
            engines=[SOURCE], index_origin=["n/a"],
            meta={"case_number": number, "county": county,
                  "filed": cells[1] if len(cells) > 1 else "",
                  "party": cells[-1] if cells else ""},
        ))
    return out


def _url(county: str, lname: str, fname: str, year: int | None) -> str:
    params = [f"db={county}"]
    if lname:
        params.append(f"lname={lname}")
    if fname:
        params.append(f"fname={fname}")
    if year:
        params += [f"FiledDateL=1/1/{year}", f"FiledDateH=12/31/{year}"]
    return f"{BASE}/Results.aspx?" + "&".join(params)


def search(county: str, lname: str = "", fname: str = "", year: int | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True, escalate: bool = True):
    """Search a county docket by party surname, optionally bounded to a year."""
    q = " ".join(f"{lname} {fname} {county} {year or ''}".split())

    # Reject an unknown county rather than searching it. OSCN answers a bad
    # `db=` with a page that parses as "no records", so a typo would read as a
    # verified absence -- a wrong negative on a records question, which is the
    # failure this package exists to prevent.
    db = county.strip().lower().replace(" ", "").replace("_", "")
    if db not in VALID_DBS:
        import difflib
        near = difflib.get_close_matches(db, sorted(VALID_DBS), n=3, cutoff=0.6)
        from ..core.results import AccessBlocker, Blocker, Coverage
        return AccessBlocker(
            query=q, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "unknown-county"}),
            mechanism=Blocker.NOT_FOUND, url=f"{BASE}/Results.aspx?db={county}",
            detail=(f"{county!r} is not an OSCN database. "
                    + (f"Did you mean: {', '.join(near)}? " if near else "")
                    + "Valid values are Oklahoma's 77 counties lowercased with no "
                      "spaces (e.g. caddo, oklahoma, tulsa, rogermills), or an "
                      "appellate db (oksc, okca, okcr). This is refused rather than "
                      "searched because OSCN answers a bad db= with a page that "
                      "parses as 'no records' -- a typo would read as an absence."))
    county = db

    return run_source(
        q, source=SOURCE, url=_url(county, lname, fname, year),
        searched=f"oscn:{county} district court docket index",
        corpus=f"Oklahoma State Courts Network, {county} County district court docket index",
        exact_match_supported=True,
        not_searched=[f"Oklahoma counties other than {county}",
                      "federal courts", "sealed, expunged, or juvenile matters",
                      "cases filed under a different name spelling"],
        caveats=["OSCN indexes FILINGS. A person with no filing in this county and year "
                 "is absent from this index, which is not the same as having no record."],
        parse=lambda html: _parse_results(html, county),
        absent_when=_no_records,
        escalate=escalate,
        store=store, limiter=limiter, use_cache=use_cache,
        cache_params={"county": county, "lname": lname, "fname": fname, "year": year},
    )


def _parse_case(county: str, number: str):
    """Build a parser for one case page. Closure, not a global: concurrent
    searches in one process would race on shared state."""
    def parse(html: str) -> list[Result]:
        soup = BeautifulSoup(html, "html.parser")
        text = re.sub(r"\n\s*\n+", "\n", soup.get_text("\n"))
        lines = [l.strip() for l in text.split("\n") if l.strip()]

        def after(label, n=1):
            for i, l in enumerate(lines):
                if l.startswith(label):
                    return l[len(label):].strip() or (lines[i + n] if i + n < len(lines) else "")
            return ""

        meta = {
            "case_number": number, "county": county,
            "caption": next((l for l in lines if " VS." in l.upper()), ""),
            "filed": after("Filed:"), "judge": after("Judge:"),
            "counts": [l for l in lines if re.search(r"\((DISMISSED|CONVICTED|PENDING)\)", l)],
            "parties": [], "attorneys": [],
        }
        if "Parties" in lines:
            i = lines.index("Parties")
            for l in lines[i + 1:i + 25]:
                if l in ("Attorneys", "Events", "Docket"):
                    break
                if re.search(r"\b(Defendant|Plaintiff|ARRESTING|DISTRICT ATTORNEY|Petitioner|Respondent)\b", l):
                    meta["parties"].append(l)

        return [Result(url=f"{BASE}/GetCaseInformation.aspx?db={county}&number={number}",
                       title=meta["caption"] or number,
                       snippet=f"{meta['filed']} {meta['judge']}".strip(),
                       source=f"{SOURCE}:{county}", engines=[SOURCE], meta=meta)]
    return parse


def case(county: str, number: str, store: Store | None = None,
         limiter: Limiter | None = None, do_archive: bool = True,
         escalate: bool = True):
    """Fetch one case page; archive the HTML with SHA-256 on retrieval."""
    return run_source(
        f"{county} {number}", source=SOURCE,
        url=f"{BASE}/GetCaseInformation.aspx?db={county}&number={number}",
        searched=f"oscn:{county} case {number}",
        parse=_parse_case(county, number),
        escalate=escalate,
        archive_as=f"oscn-{county}-{number}.html" if do_archive else None,
        store=store, limiter=limiter,
        use_cache=False,   # a case page is a document; archive is the record
    )
