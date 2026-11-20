"""CourtListener — federal and state court records.

Rate limits (wiki.free.law, v4 overview, fetched 2026-08-19): 5/min, 50/hr,
125/day authenticated; windows apply CONCURRENTLY and "the most restrictive one
-- given your recent traffic -- is what controls". Unauthenticated is stricter.

Two access facts learned the hard way on 2026-08-19:
  * courtlistener.com/recap 403s to automated fetch, but the SAME PDFs are
    served from storage.courtlistener.com.
  * The dockets/docket-entries REST endpoints are auth-walled, while the search
    API with type=r / type=rd plus cursor pagination is open.
"""
from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..core.archive import archive
from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, Probe,
                            RateLimited, Result, absence_meta, replay_cached,
                            verified_absence)
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import CachePoisoned, Store, cache_key

API = "https://www.courtlistener.com/api/rest/v4"
BASE = "https://www.courtlistener.com"
SOURCE = "courtlistener"


def _token_file() -> str:
    explicit = os.environ.get("COURTLISTENER_TOKEN_FILE")
    if explicit:
        return explicit
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "courtlistener", "token")


def _token() -> str | None:
    """The API token: the environment first, then a file.

    The file exists because the variable alone is not enough. A token exported
    from a shell profile reaches login shells only; agent tool shells, cron and
    cloud setup scripts start without it, and every call then ran on the
    anonymous tier while the caller believed it was authenticated (2026-10-10:
    401s on docket endpoints with a paid membership in place).
    """
    tok = (os.environ.get("COURTLISTENER_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        with open(_token_file(), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _headers():
    tok = _token()
    return {"Authorization": f"Token {tok}"} if tok else {}


def _parse(body: str, query: str = "", store=None) -> list[Result]:
    """Parse a search response.

    CourtListener reports the TOTAL match count alongside the (paged) rows.
    Discarding it silently turned every "count the X" question into a floor:
    a live worker counting an industry could only write ">=20 dockets" when the
    API had plainly said 155. A floor reported as a finding understates by
    whatever the page cap happens to be, which is not a small error on a beat
    where scale IS the claim. Carry it on every Result so the caller can say
    what is actually true.
    """
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"non-JSON response (auth wall or throttle): {e}") from e

    # `results` must be there and be a list. An error body ({"detail": ...})
    # has neither, and a `count` of 155 beside an empty page is a cursor or
    # paging failure -- json_rows refuses both rather than letting them read
    # as "no dockets".
    rows = json_rows(data, "results", total_key="count")
    if isinstance(rows, RecognizedEmpty):
        return rows
    total = data.get("count")
    returned = len(rows)
    out = []
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError(f"a result row is {type(r).__name__}, not an object")
        # RECAP rows carry `docket_absolute_url`; opinion rows carry
        # `absolute_url`. Taking only the latter emitted an empty url for every
        # RECAP hit while `docket_id` -- everything needed to build the link --
        # sat unused in the same response.
        path = r.get("absolute_url") or r.get("docket_absolute_url") or ""
        if not path and r.get("docket_id"):
            path = f"/docket/{r['docket_id']}/"

        # Curated view in the result; FULL record in the store.
        record_id = f"{SOURCE}:{r.get('docket_id') or r.get('id') or r.get('docketNumber') or r.get('docket_number')}"
        if store is not None:
            try:
                store.put_record(record_id, SOURCE, query, r)
            except Exception:
                pass   # a storage failure must never lose the caller's results

        meta = {k: r.get(k) for k in (
            # identity
            "docketNumber", "docket_number", "court", "court_id",
            "court_citation_string", "dateFiled", "date_filed",
            "dateTerminated", "docket_id",
            # document-level fields (`--type rd`, reported blank in FEEDBACK 2026-09-20)
            "entry_number", "document_number", "description", "short_description",
            "filepath_local",
            # posture -- who sued whom, over what, before whom
            "cause", "suitNature", "jurisdictionType", "assignedTo", "status",
            # WHO RETAINED THE EXPERT: the money question, and the reason this
            # curation exists at all
            "firm", "attorney", "party",
        ) if r.get(k) not in (None, "", [], {})}
        nxt = data.get("next") or ""
        next_cursor = ""
        if nxt and "cursor=" in nxt:
            from urllib.parse import unquote as _unq
            next_cursor = _unq(nxt.split("cursor=", 1)[1].split("&")[0])
        meta.update({"record_id": record_id, "total_matches": total,
                     "returned_this_page": returned,
                     "more_available": bool(nxt),
                     "next_cursor": next_cursor,
                     "paging_note": ("v4 pages by CURSOR, not page number. Pass "
                                     "meta.next_cursor to --cursor for the next page.")})

        # The heaviest field, summarised: descriptions are what say whether a
        # filing is an expert disclosure. Full text stays in the record.
        docs = r.get("recap_documents") or []
        if docs:
            meta["recap_documents"] = [
                {"description": d.get("description"), "url": d.get("absolute_url"),
                 "entry_number": d.get("entry_number") or d.get("document_number"),
                 "pdf_url": (f"https://storage.courtlistener.com/{d['filepath_local'].lstrip('/')}"
                             if d.get("filepath_local") else None)}
                for d in docs[:10] if isinstance(d, dict)]
            meta["recap_document_count"] = len(docs)
        if r.get("filepath_local"):
            meta["pdf_url"] = f"https://storage.courtlistener.com/{str(r['filepath_local']).lstrip('/')}"

        # `--type rd` rows often omit camelCase `caseName`/`docketNumber`/`dateFiled`
        # and instead carry `case_name`, `description`, `short_description`,
        # `entry_number`, `date_filed` (FEEDBACK.md 2026-09-20 Friction 3).
        case_name = r.get("caseName") or r.get("case_name") or ""
        doc_desc = (r.get("description") or r.get("short_description") or "").strip()
        entry_num = r.get("entry_number") or r.get("document_number")
        if case_name and entry_num and doc_desc:
            title = f"{case_name} — Doc. {entry_num}: {doc_desc}"
        elif case_name:
            title = case_name
        elif entry_num and doc_desc:
            title = f"Doc. {entry_num}: {doc_desc}"
        elif doc_desc:
            title = doc_desc
        elif entry_num:
            title = f"Doc. {entry_num}"
        else:
            title = path or ""

        court_str = r.get("court") or r.get("court_citation_string") or r.get("court_id") or ""
        docket_num = r.get("docketNumber") or r.get("docket_number") or ""
        date_filed = r.get("dateFiled") or r.get("date_filed") or ""
        if court_str or docket_num or date_filed:
            snippet = f"{court_str} | {docket_num} | {date_filed}"
            if doc_desc and doc_desc not in title:
                snippet = f"{snippet} | {doc_desc[:120]}"
        elif doc_desc:
            snippet = doc_desc[:140]
        else:
            snippet = f"{court_str} | {docket_num} | {date_filed}"

        out.append(Result(
            url=f"{BASE}{path}" if path.startswith("/") else path,
            title=title, source=SOURCE, engines=[SOURCE],
            snippet=snippet,
            meta=meta,
        ))
    return out


def search(query: str, kind: str = "r", court: str | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True, cursor: str | None = None):
    """Search. kind: r=RECAP dockets, rd=documents, o=opinions, p=people."""
    store = store or Store()
    url = f"{API}/search/?q={quote(query)}&type={kind}"
    if court:
        url += f"&court={court}"
    if cursor:
        url += f"&cursor={quote(cursor)}"
    return run_source(
        query, source=SOURCE, url=url,
        searched=f"courtlistener search (type={kind})",
        parse=lambda b: _parse(b, query, store), store=store, limiter=limiter, use_cache=use_cache,
        corpus=("CourtListener: federal + some state courts. RECAP holds only what a user "
                "has purchased from PACER and uploaded."),
        exact_match_supported=query.strip().startswith('"'),
        not_searched=["state courts absent from CourtListener",
                      "PACER documents nobody has purchased into RECAP",
                      "sealed and expunged matters"],
        caveats=([] if query.strip().startswith('"') else
                 ["UNQUOTED query: CourtListener tokenises, so this searched for the WORDS, "
                  "not the phrase. Re-run quoted before relying on this negative."]),
        cache_params={"kind": kind, "court": court, "cursor": cursor}, headers=_headers(),
    )


# --- Issue #18: Docket-watch mode (`courtlistener --docket <court>:<number>`) ---

_DEADLINE_RE = re.compile(
    r"\b((?:Answer|Responses?|Replies|Reply|Briefs?|Motions?|Objections?|Status\s+Report|"
    r"Supplemental\s+\w+|Evidentiary\s+Hearing|Hearing)\s+(?:is\s+|are\s+)?(?:due|set\s+for|scheduled\s+for)\b[^.;\n]{3,120})",
    re.I,
)
_RELATED_CASE_RE = re.compile(
    r"(?:notice\s+of\s+related\s+case|related\s+case[s]?\s*:?)\s*([^.\n]{3,140})"
    r"|(\b\d{1,2}:\d{2}-[a-z]{2}-\d{4,6}(?:-[A-Z]+)?\b)",
    re.I,
)
_STORAGE_PDF_RE = re.compile(
    r"https?://storage\.courtlistener\.com/recap/[^\s\"'<>]+\.pdf", re.I
)


def _extract_deadlines(text: str) -> list[str]:
    return sorted({re.sub(r"\s+", " ", m.group(1)).strip()
                   for m in _DEADLINE_RE.finditer(text or "")})


def _extract_related_cases(text: str, own_docket_number: str = "") -> list[str]:
    own = (own_docket_number or "").strip().lower()
    found: set[str] = set()
    low = (text or "").lower()
    if "related case" not in low:
        return []
    for m in _RELATED_CASE_RE.finditer(text or ""):
        val = re.sub(r"\s+", " ", (m.group(1) or m.group(2) or "")).strip()
        if val and val.lower() != own and own not in val.lower():
            found.add(val)
    return sorted(found)


def _parse_docket_html(html: str) -> dict:
    """Extract metadata (`last_updated`, `date_last_filing`, `case_name`) and
    docket entries from a CourtListener RECAP docket HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")

    def _meta_after(pattern: str) -> str:
        m = re.search(pattern, text, re.I)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    last_updated = _meta_after(r"Last\s+Updated\s*:\s*([^\n]+)")
    date_last_filing = _meta_after(r"Date\s+of\s+Last\s+Known\s+Filing\s*:\s*([^\n]+)")
    h1 = soup.find("h1")
    case_name = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)).strip() if h1 else ""

    entries: list[dict] = []
    # CourtListener docket entries live in `.recap-documents` rows / divs with
    # id="entry-<N>" or table rows with entry number, date, description, and PDF links.
    for row in soup.select("[id^='entry-'], tr.docket-entry, div.docket-entry-row, tr"):
        row_id = row.get("id") or ""
        row_text = re.sub(r"\s+", " ", row.get_text(" ", strip=True)).strip()
        if not row_text:
            continue
        num = ""
        if row_id.startswith("entry-"):
            num = row_id.removeprefix("entry-").strip()
        cells = [re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip()
                 for c in row.find_all(["td", "th"])]
        date_str = ""
        desc = ""
        if len(cells) >= 3 and re.fullmatch(r"\d+", cells[0] or num):
            num = num or cells[0]
            date_str = cells[1]
            desc = " ".join(c for c in cells[2:] if c)
        elif num:
            m_dt = re.search(r"\b(\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z.]*\s+\d{1,2},\s+\d{4})\b", row_text, re.I)
            date_str = m_dt.group(1) if m_dt else ""
            desc = row_text
        else:
            continue

        pdfs = []
        for a in row.find_all("a", href=True):
            href = a["href"]
            if "storage.courtlistener.com" in href and href.lower().endswith(".pdf"):
                pdfs.append(href)
        entries.append({
            "entry_number": num,
            "date_filed": date_str,
            "description": desc,
            "pdf_urls": sorted(set(pdfs)),
            "is_text_only": ("text-only" in desc.lower() or "minute order" in desc.lower()) and not pdfs,
        })

    return {
        "case_name": case_name,
        "last_updated": last_updated,
        "date_last_filing": date_last_filing,
        "entries": entries,
    }


def _norm_date(s: str) -> str:
    """Normalize a YYYY-MM-DD or 'Sept. 16, 2026' date to YYYY-MM-DD when possible."""
    s = (s or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z.]*\s+(\d{1,2}),\s+(\d{4})", s, re.I)
    if m:
        months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                  "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12}
        mo = months.get(m.group(1).lower().rstrip("."))
        if mo:
            return f"{int(m.group(3)):04d}-{mo:02d}-{int(m.group(2)):02d}"
    return s


def docket(spec: str, *, since: str | None = None, grep_pattern: str | None = None,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Docket-watch mode (#18): `courtlistener --docket <court>:<docket_number>`.

    A bare docket number matches across districts (14 dockets for one number on
    2026-09-29), so `<court>:<number>` is required. Returns docket entries with
    full text (not a 200-char grep window), `Last Updated` / `Date of Last Known
    Filing`, extracted deadlines, related-case links, and RECAP storage PDF URLs.

    Absence semantics: "no entry matching X as of <Last Updated>", and never
    certifies an absence without that timestamp, because RECAP is a mirror.
    """
    store = store or Store()
    limiter = limiter or Limiter(store)

    raw_spec = (spec or "").strip()
    if ":" not in raw_spec or not re.fullmatch(
        r"[a-z][a-z0-9_.-]+", raw_spec.split(":", 1)[0].strip(), re.I
    ):
        return AccessBlocker(
            query=raw_spec,
            coverage=Coverage(queried=[SOURCE], errored={SOURCE: "missing-court"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url=f"{API}/search/",
            detail=(f"{raw_spec!r} has no court prefix. Docket numbers repeat across "
                    "federal districts (one number matched 14 dockets on 2026-09-29). "
                    "Pass `<court>:<docket_number>`, e.g. `dcd:1:26-cv-01352` or `mnd:0:26-cr-00123`. "
                    "THIS IS NOT AN ABSENCE."))

    court_id, docket_num = (p.strip() for p in raw_spec.split(":", 1))
    if not court_id or not docket_num:
        return AccessBlocker(
            query=raw_spec,
            coverage=Coverage(queried=[SOURCE], errored={SOURCE: "bad-docket-spec"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url=f"{API}/search/",
            detail=f"Expected `<court>:<docket_number>`, got {raw_spec!r}. THIS IS NOT AN ABSENCE.")

    query = f"{court_id}:{docket_num}" + (f" since={since}" if since else "") + (f" grep={grep_pattern}" if grep_pattern else "")
    key = cache_key(f"{SOURCE}:docket", query, since=since or "", grep=grep_pattern or "")
    if use_cache:
        entry = store.get_entry(key, with_meta=True)
        if entry is not None:
            replayed = replay_cached(query, entry[0], entry[1], source=SOURCE,
                                     searched=f"courtlistener docket {query}", meta=entry[2])
            if replayed is not None:
                return replayed

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=query, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    t0 = time.time()
    search_url = f"{API}/search/?q={quote(f'\"{docket_num}\"')}&type=r&court={quote(court_id)}"
    body, blocked = fetch(search_url, source=SOURCE, query=query, headers=_headers())
    limiter.note_outcome(SOURCE, blocked)
    if blocked:
        blocked.coverage = Coverage(queried=[SOURCE], errored={SOURCE: "blocked"},
                                    elapsed_ms=int((time.time() - t0) * 1000))
        return blocked

    try:
        data = json.loads(body)
        rows = json_rows(data, "results", total_key="count")
    except Exception as e:
        return AccessBlocker(
            query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "parse"}),
            mechanism=Blocker.SERVER_ERROR, url=search_url,
            detail=f"payload shape changed ({e}) -- NOT an absence.")

    cov = Coverage(queried=[SOURCE], responsive=[SOURCE], indexes=["n/a"],
                   elapsed_ms=int((time.time() - t0) * 1000))
    not_searched = [
        "PACER filings nobody has yet purchased and uploaded into the RECAP mirror",
        "sealed or restricted docket entries",
        f"courts other than {court_id}",
    ]

    if isinstance(rows, RecognizedEmpty):
        probes = [Probe(source=SOURCE, endpoint=search_url, query=docket_num,
                        params={"court": court_id, "since": since or "", "grep": grep_pattern or ""},
                        corpus=f"CourtListener RECAP dockets in court={court_id}",
                        result_count=0, exact_match_supported=True, at=time.time())]
        caveats = ["RECAP is a crowd-sourced mirror of PACER: a docket nobody has ever "
                   "purchased in RECAP will not appear here even if it exists on PACER."]
        if use_cache:
            try:
                store.put(key, SOURCE, [], ttl_s=3600,
                          meta=absence_meta(probes, not_searched, caveats, rows.marker))
            except CachePoisoned:
                pass
        return verified_absence(
            query, cov, f"courtlistener docket {court_id}:{docket_num}",
            probes=probes, not_searched=not_searched, caveats=caveats)

    # Separate the primary docket from any related cases that matched the quoted
    # docket number (e.g. a second case whose "Notice of Related Case" cites it).
    primary = None
    related_dockets: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        dn = (r.get("docketNumber") or r.get("docket_number") or "").strip()
        if primary is None and (dn.lower() == docket_num.lower() or docket_num.lower() in dn.lower()):
            primary = r
        else:
            related_dockets.append(r)
    if primary is None:
        primary = rows[0]
        related_dockets = rows[1:]

    docket_id = primary.get("docket_id") or primary.get("id")
    docket_path = primary.get("docket_absolute_url") or primary.get("absolute_url") or (
        f"/docket/{docket_id}/" if docket_id else "")
    docket_url = f"{BASE}{docket_path}" if docket_path.startswith("/") else docket_path
    case_name = primary.get("caseName") or primary.get("case_name") or docket_num
    last_updated = (primary.get("dateModified") or primary.get("date_modified")
                    or primary.get("date_last_filing") or "")
    date_last_filing = (primary.get("date_last_filing") or primary.get("dateLastFiling")
                        or primary.get("dateFiled") or "")

    # Collect entries from API `recap_documents` and, if a docket page URL is
    # available, fetch the docket HTML page (charged to host:courtlistener.com,
    # not the 5/min API budget!) to read `Last Updated`, full entry text, and PDF links.
    entries: list[dict] = []
    for d in (primary.get("recap_documents") or []):
        if not isinstance(d, dict):
            continue
        fp = d.get("filepath_local") or ""
        pdf_url = f"https://storage.courtlistener.com/{fp.lstrip('/')}" if fp else ""
        desc = (d.get("description") or d.get("short_description") or "").strip()
        entries.append({
            "entry_number": str(d.get("entry_number") or d.get("document_number") or ""),
            "date_filed": _norm_date(d.get("date_filed") or d.get("dateFiled") or ""),
            "description": desc,
            "pdf_urls": [pdf_url] if pdf_url else [],
            "is_text_only": ("text-only" in desc.lower() or "minute order" in desc.lower()) and not pdf_url,
        })

    if docket_url:
        page_url = docket_url + ("&" if "?" in docket_url else "?") + "order_by=desc"
        page_html, page_blocked = fetch(page_url, source="host:courtlistener.com", query=query)
        if page_html and not page_blocked:
            parsed_html = _parse_docket_html(page_html)
            last_updated = parsed_html["last_updated"] or last_updated
            date_last_filing = parsed_html["date_last_filing"] or date_last_filing
            if parsed_html["entries"]:
                # Merge HTML entries with API recap_documents by entry_number
                by_num = {e["entry_number"]: e for e in entries if e["entry_number"]}
                merged_entries = []
                for he in parsed_html["entries"]:
                    en = he["entry_number"]
                    prior = by_num.pop(en, None)
                    pdfs = sorted(set((he.get("pdf_urls") or []) + ((prior or {}).get("pdf_urls") or [])))
                    desc = he["description"] if len(he["description"]) >= len((prior or {}).get("description", "")) else prior["description"]
                    merged_entries.append({
                        "entry_number": en,
                        "date_filed": _norm_date(he["date_filed"] or (prior or {}).get("date_filed", "")),
                        "description": desc,
                        "pdf_urls": pdfs,
                        "is_text_only": ("text-only" in desc.lower() or "minute order" in desc.lower()) and not pdfs,
                    })
                merged_entries.extend(by_num.values())
                entries = merged_entries

    all_deadlines: list[str] = []
    all_related: list[str] = [
        f"{rd.get('caseName') or rd.get('case_name') or '?'} ({rd.get('docketNumber') or rd.get('docket_number') or '?'})"
        for rd in related_dockets if isinstance(rd, dict)
    ]
    for e in entries:
        all_deadlines.extend(_extract_deadlines(e["description"]))
        all_related.extend(_extract_related_cases(e["description"], docket_num))
    all_deadlines = sorted(set(all_deadlines))
    all_related = sorted(set(all_related))

    filtered = list(entries)
    if since:
        norm_since = _norm_date(since)
        filtered = [e for e in filtered if not e["date_filed"] or e["date_filed"] >= norm_since]
    if grep_pattern:
        try:
            rx = re.compile(grep_pattern, re.I)
        except re.error:
            rx = re.compile(re.escape(grep_pattern), re.I)
        filtered = [e for e in filtered if rx.search(e["description"])]

    probes = [Probe(
        source=SOURCE, endpoint=docket_url or search_url, query=query,
        params={"court": court_id, "docket": docket_num,
                "since": since or "", "grep": grep_pattern or "",
                "last_updated": last_updated or "unknown"},
        corpus=f"CourtListener RECAP docket {court_id}:{docket_num} (Last Updated: {last_updated or 'unknown'})",
        result_count=len(filtered), exact_match_supported=True, at=time.time(),
    )]

    if (since or grep_pattern) and not filtered:
        # An absence on a docket watch is ONLY valid with the Last Updated
        # timestamp, because RECAP is a mirror (#18).
        if not last_updated:
            return AccessBlocker(
                query=query,
                coverage=Coverage(queried=[SOURCE], errored={SOURCE: "missing-last-updated"}),
                mechanism=Blocker.SERVER_ERROR, url=docket_url or search_url,
                detail=("Cannot certify a docket-entry absence without the docket's "
                        "`Last Updated` timestamp (RECAP is a mirror, not the court's "
                        "live register)."))
        caveats = [
            f"RECAP mirror snapshot as of Last Updated: {last_updated}"
            + (f" (Date of Last Known Filing: {date_last_filing})" if date_last_filing else "")
            + ". Filings entered on PACER after this timestamp and not yet uploaded to RECAP are not visible here.",
            "Text-only orders carry no PDF attachment on PACER or RECAP; this check scanned entry descriptions directly.",
        ]
        marker = f"docket {docket_id} read ({len(entries)} entries, Last Updated: {last_updated}), 0 matched filter"
        if use_cache:
            try:
                store.put(key, SOURCE, [], ttl_s=1800,
                          meta=absence_meta(probes, not_searched, caveats, marker))
            except CachePoisoned:
                pass
        return verified_absence(
            query, cov,
            f"courtlistener docket {court_id}:{docket_num} ({case_name}) as of Last Updated: {last_updated}",
            probes=probes, not_searched=not_searched, caveats=caveats)

    results: list[Result] = []
    common_meta = {
        "docket_id": docket_id,
        "court": court_id,
        "docket_number": primary.get("docketNumber") or docket_num,
        "case_name": case_name,
        "last_updated": last_updated or None,
        "date_last_filing": date_last_filing or None,
        "deadlines": all_deadlines,
        "related_cases": all_related,
        "total_entries_on_page": len(entries),
    }

    if filtered:
        for e in filtered:
            en = e["entry_number"] or "?"
            pdf_url = e["pdf_urls"][0] if e["pdf_urls"] else ""
            results.append(Result(
                url=pdf_url or docket_url or search_url,
                title=f"[{case_name}] Doc. {en} ({e['date_filed'] or '?'}): {e['description'][:120]}",
                snippet=(f"Last Updated: {last_updated or '?'} | Last Filing: {date_last_filing or '?'} | "
                         + ("(Text-Only Entry — no PDF) | " if e["is_text_only"] else "")
                         + e["description"]),
                source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
                meta={
                    **common_meta,
                    "entry_number": e["entry_number"],
                    "date_filed": e["date_filed"],
                    "description": e["description"],
                    "pdf_url": pdf_url or None,
                    "pdf_urls": e["pdf_urls"],
                    "is_text_only": e["is_text_only"],
                },
            ))
    else:
        results.append(Result(
            url=docket_url or search_url,
            title=f"{case_name} ({court_id} {primary.get('docketNumber') or docket_num})",
            snippet=(f"Last Updated: {last_updated or '?'} | Last Filing: {date_last_filing or '?'} | "
                     f"Deadlines: {', '.join(all_deadlines) or 'none'} | "
                     f"Related: {', '.join(all_related) or 'none'}"),
            source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
            meta=common_meta,
        ))

    if use_cache:
        try:
            store.put(key, SOURCE, [r.__dict__ for r in results], ttl_s=1800)
        except CachePoisoned as e:
            cov.cache_write_refused = str(e)
    return Hit(query=query, coverage=cov, results=results, probes=probes)


def document(storage_url: str, filename: str, store: Store | None = None,
             limiter: Limiter | None = None):
    """Fetch a RECAP PDF from storage.courtlistener.com and archive it.

    Use the storage host: courtlistener.com/recap 403s to automated clients.
    """
    store = store or Store()
    limiter = limiter or Limiter(store)
    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=filename,
                           coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    data, blocked = fetch(storage_url, source=SOURCE, query=filename, binary=True)
    limiter.note_outcome(SOURCE, blocked)
    if blocked:
        return blocked
    info = archive(data, filename, storage_url, "evidence-search:courtlistener")
    return Hit(query=filename,
               coverage=Coverage(queried=[SOURCE], responsive=[SOURCE]),
               results=[Result(url=storage_url, title=filename, source="courtlistener",
                               engines=["courtlistener"], meta={"archived": info})])
