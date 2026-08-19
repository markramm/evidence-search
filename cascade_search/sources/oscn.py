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
import time

from bs4 import BeautifulSoup

from ..core.archive import archive
from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import Coverage, Hit, RateLimited, Result, verified_absence
from ..core.store import Store, cache_key

BASE = "https://www.oscn.net/dockets"
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
            snippet=" | ".join(cells), source=f"oscn:{county}",
            engines=["oscn"], index_origin=["n/a"],
            meta={"case_number": number, "county": county,
                  "filed": cells[1] if len(cells) > 1 else "",
                  "party": cells[-1] if cells else ""},
        ))
    return out


def search(county: str, lname: str = "", fname: str = "", year: int | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True, escalate: bool = True):
    """Search a county docket by party surname, optionally bounded to a year."""
    store = store or Store()
    limiter = limiter or Limiter(store)
    q = f"{lname} {fname} {county} {year or ''}".strip()

    key = cache_key("oscn", q, county=county, lname=lname, fname=fname, year=year)
    if use_cache:
        cached = store.get(key)
        if cached is not None:
            cov = Coverage(queried=["oscn"], responsive=["oscn"], indexes=["n/a"], cache_hits=1)
            return Hit(query=q, coverage=cov,
                       results=[Result(**r) for r in cached]) if cached else \
                   verified_absence(q, cov, f"oscn:{county}")

    allowed, retry, why = limiter.check("oscn")
    if not allowed:
        return RateLimited(query=q, coverage=Coverage(queried=["oscn"], rate_limited=["oscn"]),
                           source="oscn", retry_after_s=int(retry) if retry else None, detail=why)

    params = [f"db={county}"]
    if lname:
        params.append(f"lname={lname}")
    if fname:
        params.append(f"fname={fname}")
    if year:
        params += [f"FiledDateL=1/1/{year}", f"FiledDateH=12/31/{year}"]
    url = f"{BASE}/Results.aspx?" + "&".join(params)

    t0 = time.time()
    html, blocked = fetch(url, source="oscn", query=q)
    limiter.record("oscn")

    # Auto-escalate: OSCN's Turnstile is exactly what the browser tier exists
    # for (spec 3a). If the browser also cannot clear it, that call returns an
    # AwaitingHuman gate rather than a dead end.
    if blocked and getattr(blocked, "escalate_to_browser", False) and escalate:
        from ..core import browser as _browser
        b_html, b_out = _browser.fetch(url, source="oscn", query=q, store=store)
        if b_html:
            html, blocked = b_html, None
        else:
            if b_out is not None:
                b_out.coverage = Coverage(queried=["oscn", "oscn:browser"],
                                          errored={"oscn": "blocked", "oscn:browser": "blocked"},
                                          elapsed_ms=int((time.time() - t0) * 1000))
                return b_out

    if blocked:
        blocked.coverage = Coverage(queried=["oscn"], errored={"oscn": str(getattr(blocked, "mechanism", "error"))},
                                    elapsed_ms=int((time.time() - t0) * 1000))
        return blocked

    cov = Coverage(queried=["oscn"], responsive=["oscn"], indexes=["n/a"],
                   elapsed_ms=int((time.time() - t0) * 1000))

    if _no_records(html):
        if use_cache:
            store.put(key, "oscn", [], ttl_s=86400)
        return verified_absence(q, cov, f"oscn:{county} district court docket index")

    results = _parse_results(html, county)
    if use_cache:
        store.put(key, "oscn", [r.__dict__ for r in results], ttl_s=86400)
    return Hit(query=q, coverage=cov, results=results)


def case(county: str, number: str, store: Store | None = None,
         limiter: Limiter | None = None, do_archive: bool = True):
    """Fetch one case page; archive the HTML with SHA-256 on retrieval."""
    store = store or Store()
    limiter = limiter or Limiter(store)
    q = f"{county} {number}"

    allowed, retry, why = limiter.check("oscn")
    if not allowed:
        return RateLimited(query=q, coverage=Coverage(queried=["oscn"], rate_limited=["oscn"]),
                           source="oscn", retry_after_s=int(retry) if retry else None, detail=why)

    url = f"{BASE}/GetCaseInformation.aspx?db={county}&number={number}"
    html, blocked = fetch(url, source="oscn", query=q)
    limiter.record("oscn")
    if blocked:
        blocked.coverage = Coverage(queried=["oscn"], errored={"oscn": "blocked"})
        return blocked

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

    arch = None
    if do_archive:
        arch = archive(html.encode(), f"oscn-{county}-{number}.html", url, "cascade-search:oscn")
        meta["archived"] = arch

    return Hit(query=q, coverage=Coverage(queried=["oscn"], responsive=["oscn"], indexes=["n/a"]),
               results=[Result(url=url, title=meta["caption"] or number,
                               snippet=f"{meta['filed']} {meta['judge']}",
                               source=f"oscn:{county}", engines=["oscn"], meta=meta)])
