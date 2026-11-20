"""SEC EDGAR — full-text search (EFTS) and company submissions by CIK/ticker (#1).

Verified live 2026-08-19 and 2026-10-08 (highest unbuilt corpus demand: 238 refs):
  * Full-text search (keyless JSON, Elasticsearch-backed, 2001+):
    `https://efts.sec.gov/LATEST/search-index?q=%22CoreCivic%22`
  * Company submissions (keyless JSON):
    `https://data.sec.gov/submissions/CIK{10-digit-padded}.json`
  * Company ticker/CIK map (keyless JSON):
    `https://www.sec.gov/files/company_tickers.json`

The research question this answers: *what did this company tell its investors,
under penalty of securities law, about the contract, facility, or risk it is
telling the government or public something else about?*

SEC fair-access discipline:
  * Declare a descriptive `User-Agent` with contact info (`EVIDENCE_SEC_UA`
    overrides the default `evidence-search/0.1 (mailto:mark.ramm@gmail.com)`).
  * Rate-limit strictly below SEC's 10 req/s ceiling (`8/s, 120/min` in the
    shared ledger).
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import quote, urlencode

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import AccessBlocker, Blocker, Coverage, Result
from ..core.source import RecognizedEmpty, Unanswerable, run_source
from ..core.store import Store, cache_key

SOURCE = "sec"
EFTS_API = "https://efts.sec.gov/LATEST/search-index"
SUBMISSIONS_API = "https://data.sec.gov/submissions"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

DEFAULT_UA = "evidence-search/0.1 (mailto:mark.ramm@gmail.com)"


def _headers() -> dict[str, str]:
    ua = (os.environ.get("EVIDENCE_SEC_UA") or DEFAULT_UA).strip()
    return {"User-Agent": ua, "Accept": "application/json"}


def format_cik(cik: str | int) -> str | None:
    s = re.sub(r"^CIK0*", "", str(cik or "").strip(), flags=re.I)
    if not s.isdigit() or not (1 <= len(s) <= 10):
        return None
    return f"{int(s):010d}"


def _parse_efts(body: str, query: str, store: Store | None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"non-JSON response: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("hits"), dict):
        raise ValueError(
            "no `hits` object in SEC EFTS response "
            f"(keys: {', '.join(sorted(map(str, data))) if isinstance(data, dict) else 'none'})")

    hits_obj = data["hits"]
    rows = hits_obj.get("hits")
    total_obj = hits_obj.get("total")
    total = total_obj.get("value") if isinstance(total_obj, dict) else (
        total_obj if isinstance(total_obj, int) else None)
    if not isinstance(rows, list):
        raise ValueError("`hits.hits` is not a list in SEC EFTS response")
    if not rows:
        if isinstance(total, bool) or (total is not None and not isinstance(total, int)):
            raise ValueError(f"unreadable `hits.total`: {total_obj!r}")
        if isinstance(total, int) and total > 0:
            raise Unanswerable(
                f"SEC EFTS reports total={total} but this page carries no rows "
                "-- a page past the end, NOT an absence.")
        return RecognizedEmpty("hits.hits: []" + (f", hits.total.value: {total}"
                                                  if total is not None else ""))

    out: list[Result] = []
    seen_entities: dict[str, str] = {}
    for h in rows:
        if not isinstance(h, dict):
            raise ValueError("an EFTS hit row is not an object")
        src = h.get("_source") or {}
        if not isinstance(src, dict):
            raise ValueError("`_source` in EFTS hit is not an object")
        hit_id = str(h.get("_id") or "")
        adsh = str(src.get("adsh") or hit_id.split(":")[0] or "").strip()
        filename = hit_id.split(":", 1)[1] if ":" in hit_id else (src.get("file_name") or "")
        ciks = src.get("ciks") or []
        cik = format_cik(ciks[0]) if ciks else None
        cik_int = int(cik) if cik else None
        names = src.get("display_names") or []
        entity = names[0] if names else (src.get("entity") or "?")
        if cik and entity:
            seen_entities[cik] = entity
        form = src.get("form") or src.get("root_forms", [""])[0] or ""
        filed = src.get("file_date") or ""
        period = src.get("period_ending") or ""
        adsh_nodash = adsh.replace("-", "")
        if cik_int and adsh_nodash and filename:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{adsh_nodash}/{filename}"
        elif cik_int and adsh_nodash:
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{adsh_nodash}/{adsh}-index.htm"
        else:
            url = f"https://www.sec.gov/edgar/searchedgar/companysearch"

        rid = f"{SOURCE}:efts:{hit_id or adsh}"
        if store is not None:
            try:
                store.put_record(rid, SOURCE, query, h)
            except Exception:
                pass

        snip_bits = [b for b in (
            f"Form {form}" if form else "",
            f"filed {filed}" if filed else "",
            f"period {period}" if period else "",
            f"accession {adsh}" if adsh else "",
            filename,
        ) if b]

        out.append(Result(
            url=url,
            title=f"{entity} — {form} ({filed})" + (f" [{filename}]" if filename else ""),
            snippet=" | ".join(snip_bits),
            source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
            meta={
                "record_id": rid,
                "cik": cik,
                "entity": entity,
                "form": form,
                "file_date": filed,
                "period_ending": period or None,
                "accession_number": adsh or None,
                "filename": filename or None,
                "total_matches": total,
            },
        ))

    if len(seen_entities) > 1 and out:
        out[0].meta["entity_caveat"] = (
            f"MATCHED {len(seen_entities)} DISTINCT SEC REGISTRANTS: "
            + ", ".join(f"{nm} (CIK {c})" for c, nm in list(seen_entities.items())[:5])
            + ". Scope with `--entity <CIK>` if you are counting filings for one issuer."
        )
    return out


_EFTS_NOT_SEARCHED = [
    "SEC filings prior to 2001 (EDGAR EFTS full-text index coverage begins in 2001)",
    "private companies, foreign issuers without U.S. reporting obligations, and unfiled offerings",
    "graphic or scanned exhibits without an extractable text layer",
]


def fulltext(query: str, *, forms: str | None = None,
             date_from: str | None = None, date_to: str | None = None,
             entity: str | None = None, page: int = 1, limit: int = 25,
             store: Store | None = None, limiter: Limiter | None = None,
             use_cache: bool = True):
    """Search SEC EDGAR full text (EFTS) across filings from 2001 onward (#1)."""
    store = store or Store()
    params = [("q", query)]
    if forms:
        params.append(("forms", ",".join(f.strip().upper() for f in forms.split(",") if f.strip())))
    if date_from or date_to:
        params.append(("dateRange", "custom"))
        if date_from:
            params.append(("startdt", date_from))
        if date_to:
            params.append(("enddt", date_to))
    if entity:
        cik = format_cik(entity)
        params.append(("ciks", cik) if cik else ("entityName", entity.strip()))
    if page > 1:
        params.append(("from", str((page - 1) * limit)))

    url = f"{EFTS_API}?{urlencode(params)}"
    exact = query.strip().startswith('"') and query.strip().endswith('"')
    return run_source(
        query, source=SOURCE, url=url,
        searched="sec edgar full-text search (EFTS, 2001+)",
        parse=lambda b: _parse_efts(b, query, store),
        headers=_headers(),
        store=store, limiter=limiter, use_cache=use_cache,
        exact_match_supported=exact,
        corpus="SEC EDGAR Full-Text Search (EFTS): electronic SEC filings from 2001 to present.",
        not_searched=list(_EFTS_NOT_SEARCHED) + (
            [f"form types other than {forms}"] if forms else []),
        caveats=([] if exact else [
            "UNQUOTED query: EFTS matches filings containing all terms anywhere in the document. "
            "Quote the phrase (`'\"exact phrase\"'`) for an exact-phrase search."
        ]),
        cache_params={"mode": "fulltext", "forms": forms, "date_from": date_from,
                      "date_to": date_to, "entity": entity, "page": page},
    )


def _resolve_company_to_ciks(company: str, store: Store, limiter: Limiter):
    """Resolve a CIK, ticker, or company name to `([(cik10, ticker, title), ...], blocker_or_None)`."""
    direct = format_cik(company)
    if direct:
        return [(direct, "", f"CIK {direct}")], None

    key = cache_key(f"{SOURCE}:tickers:v1", "all")
    roster = store.get(key)
    if not isinstance(roster, list):
        ok, retry, why = limiter.reserve(SOURCE)
        if not ok:
            from ..core.results import RateLimited
            return None, RateLimited(
                query=company, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)
        body, blocked = fetch(TICKERS_URL, source=SOURCE, query=company, headers=_headers())
        limiter.note_outcome(SOURCE, blocked)
        if blocked:
            return None, blocked
        try:
            raw = json.loads(body)
            vals = raw.values() if isinstance(raw, dict) else raw
            roster = [
                {"cik": f"{int(r['cik_str']):010d}",
                 "ticker": str(r.get("ticker") or "").upper(),
                 "title": str(r.get("title") or "")}
                for r in vals if isinstance(r, dict) and str(r.get("cik_str", "")).isdigit()
            ]
        except Exception as e:
            return None, AccessBlocker(
                query=company, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
                mechanism=Blocker.SERVER_ERROR, url=TICKERS_URL,
                detail=f"payload shape changed on company_tickers.json ({e}) -- NOT an absence.")
        store.put(key, SOURCE, roster, ttl_s=86400, is_metadata=True)

    q = company.strip().upper()
    exact_ticker = [(r["cik"], r["ticker"], r["title"]) for r in roster if r["ticker"] == q]
    if exact_ticker:
        return exact_ticker, None
    name_hits = [(r["cik"], r["ticker"], r["title"]) for r in roster if q in r["title"].upper()]
    return name_hits, None


def submissions(company: str, *, forms: str | None = None, limit: int = 25,
                store: Store | None = None, limiter: Limiter | None = None,
                use_cache: bool = True):
    """Fetch company filing history from `data.sec.gov/submissions/CIK{10d}.json` (#1)."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    matches, err = _resolve_company_to_ciks(company, store, limiter)
    if err is not None:
        return err
    if not matches:
        from ..core.results import Probe, verified_absence
        import time as _t
        cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"])
        return verified_absence(
            company, cov, f"SEC EDGAR company ticker/CIK directory for {company!r}",
            probes=[Probe(source=SOURCE, endpoint=TICKERS_URL, query=company,
                          corpus="SEC EDGAR company_tickers.json active issuer directory",
                          result_count=0, exact_match_supported=False, at=_t.time())],
            not_searched=[
                "delisted, merged, or non-exchange-traded filers absent from company_tickers.json "
                "(search their filings via `evidence-search sec --fulltext` or pass their 10-digit CIK)",
                "private companies without SEC reporting obligations",
            ],
            caveats=["Name lookup searches SEC's ticker/issuer map by substring; "
                     "pass a 10-digit CIK if the filer has no active ticker."],
        )

    cik, ticker, matched_title = matches[0]
    collision_warning = None
    if len(matches) > 1:
        collision_warning = (
            f"MATCHED {len(matches)} DISTINCT CIKs for {company!r}: "
            + ", ".join(f"{t} ({tk or 'no-ticker'}, CIK {c})" for c, tk, t in matches[:5])
            + f". Showing submissions for CIK {cik} ({matched_title}); pass the exact CIK to select another."
        )

    want_forms = {f.strip().upper() for f in (forms or "").split(",") if f.strip()}
    url = f"{SUBMISSIONS_API}/CIK{cik}.json"

    def _parse_submissions(body: str) -> list[Result]:
        try:
            d = json.loads(body)
        except json.JSONDecodeError as e:
            raise ValueError(f"non-JSON response: {e}") from e
        if not isinstance(d, dict) or not isinstance(d.get("filings"), dict):
            raise ValueError(
                "no `filings` object in SEC submissions response "
                f"(keys: {', '.join(sorted(map(str, d))) if isinstance(d, dict) else 'none'})")
        recent = d["filings"].get("recent")
        if not isinstance(recent, dict) or not isinstance(recent.get("accessionNumber"), list):
            raise ValueError("no `filings.recent.accessionNumber` list in SEC submissions response")

        name = d.get("name") or matched_title
        acc_list = recent.get("accessionNumber") or []
        form_list = recent.get("form") or []
        date_list = recent.get("filingDate") or []
        rep_list = recent.get("reportDate") or []
        doc_list = recent.get("primaryDocument") or []
        desc_list = recent.get("primaryDocDescription") or []

        if not acc_list:
            return RecognizedEmpty(f"CIK {cik} ({name}): filings.recent.accessionNumber: []")

        cik_int = int(cik)
        out: list[Result] = []
        for i, acc in enumerate(acc_list):
            fm = (form_list[i] if i < len(form_list) else "").strip()
            if want_forms and fm.upper() not in want_forms:
                continue
            dt = date_list[i] if i < len(date_list) else ""
            rep = rep_list[i] if i < len(rep_list) else ""
            doc = doc_list[i] if i < len(doc_list) else ""
            desc = desc_list[i] if i < len(desc_list) else ""
            acc_nodash = str(acc).replace("-", "")
            doc_url = (f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"
                       if doc else f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{acc}-index.htm")
            rid = f"{SOURCE}:sub:{cik}:{acc}"
            row_payload = {
                "cik": cik, "name": name, "accessionNumber": acc, "form": fm,
                "filingDate": dt, "reportDate": rep, "primaryDocument": doc,
                "primaryDocDescription": desc, "url": doc_url,
            }
            try:
                store.put_record(rid, SOURCE, company, row_payload)
            except Exception:
                pass
            out.append(Result(
                url=doc_url,
                title=f"{name} (CIK {cik}) — Form {fm} filed {dt}",
                snippet=" | ".join(x for x in (
                    f"period {rep}" if rep else "",
                    f"accession {acc}",
                    desc or doc,
                ) if x),
                source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
                meta={
                    "record_id": rid,
                    "cik": cik,
                    "company_name": name,
                    "tickers": d.get("tickers"),
                    "sic": d.get("sic"),
                    "sic_description": d.get("sicDescription"),
                    "form": fm,
                    "filing_date": dt,
                    "report_date": rep or None,
                    "accession_number": acc,
                    "primary_document": doc or None,
                    "entity_caveat": collision_warning,
                    "total_matches": len(acc_list) if not want_forms else None,
                },
            ))
            if len(out) >= limit:
                break

        if not out and want_forms:
            return RecognizedEmpty(
                f"CIK {cik} ({name}): 0 filings matching forms={','.join(sorted(want_forms))} "
                f"in {len(acc_list)} recent submissions")
        return out

    return run_source(
        f"{company} (CIK {cik})" + (f" forms={forms}" if forms else ""),
        source=SOURCE, url=url,
        searched=f"sec edgar submissions for {matched_title} (CIK {cik})",
        parse=_parse_submissions,
        headers=_headers(),
        store=store, limiter=limiter, use_cache=use_cache,
        answer_on={Blocker.NOT_FOUND: f"Rests on an HTTP 404 for CIK {cik} on data.sec.gov."},
        exact_match_supported=True,
        corpus=f"SEC EDGAR submissions history for CIK {cik} ({matched_title}).",
        not_searched=(
            [f"form types other than {forms}"] if forms else []
        ) + ["older archive blocks in filings.files[] beyond filings.recent (~1,000 most recent filings)"],
        caveats=([collision_warning] if collision_warning else []),
        cache_params={"mode": "submissions", "cik": cik, "forms": forms, "limit": limit},
    )
