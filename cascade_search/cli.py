"""cascade-search CLI.

Primary interface for Claude Code workers: Bash-native, pipeable, and it costs
no context until invoked. An MCP shim over the same core is Phase 2.
"""
from __future__ import annotations

import argparse
import json
import sys

from .core.results import AccessBlocker, AwaitingHuman, Hit, RateLimited, VerifiedAbsence
from .core.store import Store
from .core.limits import Limiter


def _emit(outcome, as_json: bool) -> int:
    """Print, and return an exit code that encodes the OUTCOME TYPE.

    Exit codes let a shell caller branch without parsing:
      0 hits | 1 verified absence | 2 blocked | 3 rate-limited | 4 awaiting human
    """
    if as_json:
        print(json.dumps(outcome.to_dict(), indent=2, default=str))
    else:
        kind = type(outcome).__name__
        print(f"== {kind} ==")
        print(f"query:    {outcome.query}")
        print(f"coverage: {outcome.coverage.summary()}")
        if isinstance(outcome, Hit):
            print(f"results:  {len(outcome.results)}\n")
            for i, r in enumerate(outcome.results, 1):
                mark = " *UNIQUE*" if r.unique_to_engine and len(r.engines) else ""
                print(f"{i:3}. {r.title[:100]}{mark}")
                # A 320-char opaque redirect is noise, not a citation. Show the
                # publisher -- which IS in the feed -- and say plainly that the
                # link will not serve as a source.
                if r.meta.get("citable") is False:
                    pub = r.meta.get("publisher") or "publisher"
                    print(f"     [{pub}] -- redirect only, not citable; find it on "
                          f"{pub}'s site or via `web`")
                else:
                    print(f"     {r.url}")
                if r.snippet:
                    print(f"     {r.snippet[:140]}")
        elif isinstance(outcome, VerifiedAbsence):
            print(f"searched: {outcome.searched}")
            print("\nPUBLISHABLE NEGATIVE: the right corpus was searched by the right")
            print("method and every engine responded. This is a finding, not a failure.")
        elif isinstance(outcome, AccessBlocker):
            print(f"mechanism: {outcome.mechanism.value}")
            print(f"url:       {outcome.url}")
            print(f"detail:    {outcome.detail}")
            print("\nNOT a negative finding. Access was blocked.")
            if outcome.escalate_to_browser:
                print("ESCALATABLE: a real browser session could plausibly pass this gate.")
        elif isinstance(outcome, RateLimited):
            print(f"source:    {outcome.source}")
            print(f"retry after: {outcome.retry_after_s}s")
            print(f"detail:    {outcome.detail}")
            print("\nTOOLING-LIMITED NEGATIVE -- explicitly NOT content-exhausted. Retry.")
        elif isinstance(outcome, AwaitingHuman):
            print(f"gate:  {outcome.gate_type.value}")
            print(f"url:   {outcome.url}")
            print(f"token: {outcome.resume_token}")
            print(f"\n{outcome.instructions}")
    return {Hit: 0, VerifiedAbsence: 1, AccessBlocker: 2,
            RateLimited: 3, AwaitingHuman: 4}.get(type(outcome), 5)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cascade-search",
                                description="Federated research client with typed outcomes.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--wait", action="store_true",
                   help="block for short spacing waits instead of returning RateLimited "
                        "(sub-minute only; never waits out a real budget window)")
    sub = p.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("news", help="Google News RSS (free, keyless)")
    n.add_argument("query")

    o = sub.add_parser("oscn", help="Oklahoma State Courts Network")
    o.add_argument("--county",
                   help="Oklahoma county lowercased, no spaces (caddo, oklahoma, "
                        "tulsa, rogermills) or an appellate db (oksc/okca/okcr). "
                        "--list-counties to see all 77.")
    o.add_argument("--lname"); o.add_argument("--fname")
    o.add_argument("--year", type=int); o.add_argument("--case")
    o.add_argument("--list-counties", action="store_true", help="print valid db= values")

    c = sub.add_parser("courtlistener", help="CourtListener (5/min, 50/hr, 125/day)")
    c.add_argument("query", help='QUOTE phrases when counting: "Force Science '
                                 'Institute" -> 155 matches, unquoted -> 51,622')
    c.add_argument("--type", default="r", choices=["r", "rd", "o", "p"])
    c.add_argument("--court")
    c.add_argument("--cursor",
                   help="next page: pass meta.next_cursor from a prior result "
                        "(v4 pages by cursor, not page number)")

    pp = sub.add_parser("propublica", help="ProPublica Trump-team financial disclosures")
    pp.add_argument("query")

    w = sub.add_parser("web", help="general web via a local SearXNG container")
    w.add_argument("query")
    w.add_argument("--categories", default="general",
                   help="general, news, science, files, images…")
    w.add_argument("--page", type=int, default=1)
    w.add_argument("--base", help="instance URL (default $SEARXNG_URL or 127.0.0.1:8888)")

    us = sub.add_parser("usaspending", help="federal awards (no key; real counts)")
    us.add_argument("query")
    us.add_argument("--count", action="store_true",
                    help="return the REAL total by award type instead of a page of awards")
    us.add_argument("--keywords", action="store_true",
                    help="full-text keyword search (FUZZY) instead of recipient-name match")
    us.add_argument("--all-types", action="store_true",
                    help="grants/loans/direct payments too, not just contracts")
    us.add_argument("--from", dest="date_from", help="award start on/after YYYY-MM-DD")
    us.add_argument("--to", dest="date_to", help="award start on/before YYYY-MM-DD")
    us.add_argument("--page", type=int, default=1)
    us.add_argument("--limit", type=int, default=25)

    cr = sub.add_parser("crossref", help="scholarly records; DOI lookup is exact")
    cr.add_argument("query", help="a DOI (exact) or title words (FUZZY discovery)")
    cr.add_argument("--rows", type=int, default=10)

    fr = sub.add_parser("fedreg", help="Federal Register rules/notices (real counts)")
    fr.add_argument("query")
    fr.add_argument("--type", dest="doc_type",
                    choices=["rule", "proposed", "notice", "presidential"],
                    help="filter by document type")
    fr.add_argument("--agency", help="agency slug, e.g. homeland-security-department")
    fr.add_argument("--from", dest="date_from", help="published on/after YYYY-MM-DD")
    fr.add_argument("--to", dest="date_to", help="published on/before YYYY-MM-DD")
    fr.add_argument("--page", type=int, default=1)
    fr.add_argument("--per-page", type=int, default=20)

    dc = sub.add_parser("docs", help="search a documentation site's index (no key)")
    dc.add_argument("query")
    dc.add_argument("--site", default="claude-code")
    dc.add_argument("--fetch-top", type=int, default=0, help="also fetch N page bodies")

    b = sub.add_parser("browser", help="fetch a public-records page with a real browser")
    b.add_argument("url")
    b.add_argument("--headed", action="store_true", help="visible browser (passes most gates)")
    b.add_argument("--wait-ms", type=int, default=4000)
    b.add_argument("--save", help="write the HTML here and archive it")

    g = sub.add_parser("gate", help="humanomation: list and resume human gates")
    gs = g.add_subparsers(dest="gate_cmd", required=True)
    gs.add_parser("list")
    gu = gs.add_parser("ui", help="open the gate worklist in a browser (easiest path)")
    gu.add_argument("--port", type=int, default=8787)
    gu.add_argument("--no-open", action="store_true", help="print the URL, do not launch a browser")
    gr = gs.add_parser("resume"); gr.add_argument("token"); gr.add_argument("--file", action="append")

    ex = sub.add_parser("extract", help="local dynamic filtering: fields, not pages")
    ex.add_argument("target", help="URL or local file")
    ex.add_argument("--grep", action="append", help="regex; repeatable. Returns matching passages only")
    ex.add_argument("--ids", action="store_true", help="extract government identifiers")
    ex.add_argument("--tables", action="store_true", help="extract tables as rows")
    ex.add_argument("--text", action="store_true", help="readable text, chrome stripped")
    ex.add_argument("--max-chars", type=int, default=0)
    ex.add_argument("--browser", action="store_true", help="fetch via browser tier")
    ex.add_argument("--no-ocr", action="store_true",
                    help="do not OCR a scanned PDF; report it as unreadable instead")
    ex.add_argument("--ocr-pages", type=int, default=50,
                    help="page cap when OCR is used (default 50)")

    rec = sub.add_parser("record", help="fetch the FULL upstream record behind a result")
    rec.add_argument("record_id", nargs="?",
                     help="record_id from a result's meta (e.g. courtlistener:5409345)")
    rec.add_argument("--list", action="store_true", help="list stored records")
    rec.add_argument("--source", help="filter --list by source")
    rec.add_argument("--query", help="filter --list by the query that produced it")
    rec.add_argument("--fields", help="comma-separated fields to return")

    sub.add_parser("limits", help="show per-source rate policy and current usage")

    a = p.parse_args(argv)
    store = Store(); limiter = Limiter(store); use_cache = not a.no_cache
    if getattr(a, "wait", False):
        limiter.wait_for_spacing = True

    if a.cmd == "news":
        from .engines.news_rss import search
        return _emit(search(a.query, store, limiter, use_cache), a.json)

    if a.cmd == "oscn":
        from .sources import oscn
        if a.list_counties:
            print("Counties (77):")
            for i, c_ in enumerate(sorted(oscn.COUNTIES)):
                print(f"  {c_:16}", end="\n" if i % 4 == 3 else "")
            print("\n\nAppellate:", ", ".join(sorted(oscn.APPELLATE)))
            return 0
        if a.case:
            return _emit(oscn.case(a.county, a.case, store, limiter), a.json)
        if not a.county:
            print("--county is required (or use --list-counties)", file=sys.stderr); return 2
        return _emit(oscn.search(a.county, a.lname or "", a.fname or "",
                                 a.year, store, limiter, use_cache), a.json)

    if a.cmd == "courtlistener":
        from .sources import courtlistener
        return _emit(courtlistener.search(a.query, a.type, a.court, store, limiter,
                                          use_cache, cursor=a.cursor), a.json)

    if a.cmd == "propublica":
        from .sources import propublica_disclosures
        return _emit(propublica_disclosures.search(a.query, store, limiter, use_cache), a.json)

    if a.cmd == "web":
        from .engines.searxng import search as web_search
        return _emit(web_search(a.query, a.categories, a.page, a.base,
                                store, limiter, use_cache), a.json)

    if a.cmd == "usaspending":
        from .sources import usaspending as usa
        types = usa.ALL_AWARD_TYPES if a.all_types else usa.CONTRACT_TYPES
        if a.count:
            return _emit(usa.counts(a.query, by_recipient=not a.keywords,
                                    award_types=types, date_from=a.date_from,
                                    date_to=a.date_to, store=store, limiter=limiter), a.json)
        return _emit(usa.search(a.query, by_recipient=not a.keywords, award_types=types,
                                date_from=a.date_from, date_to=a.date_to,
                                limit=a.limit, page=a.page, store=store,
                                limiter=limiter, use_cache=use_cache), a.json)

    if a.cmd == "crossref":
        from .sources import crossref as cr_src
        return _emit(cr_src.search(a.query, a.rows, store, limiter, use_cache), a.json)

    if a.cmd == "fedreg":
        from .sources import federal_register as fr_src
        return _emit(fr_src.search(a.query, a.doc_type, a.agency, a.date_from,
                                   a.date_to, a.per_page, a.page, store,
                                   limiter, use_cache), a.json)

    if a.cmd == "docs":
        from .sources import docs as docs_src
        return _emit(docs_src.search(a.query, a.site, a.fetch_top, store, limiter, use_cache), a.json)

    if a.cmd == "browser":
        from .core import browser
        from .core.archive import archive as _archive
        html, out = browser.fetch(a.url, source="browser", query=a.url,
                                  wait_ms=a.wait_ms, headless=not a.headed, store=store)
        if out:
            return _emit(out, a.json)
        rec = None
        if a.save:
            from pathlib import Path as _P
            _P(a.save).write_text(html)
            rec = _archive(html.encode(), _P(a.save).name, a.url, "cascade-search:browser")
        from .core.results import Hit as _Hit, Result as _R, Coverage as _C
        return _emit(_Hit(query=a.url, coverage=_C(queried=["browser"], responsive=["browser"]),
                          results=[_R(url=a.url, title=f"{len(html)} bytes retrieved",
                                      source="browser", engines=["browser"],
                                      meta={"archived": rec, "bytes": len(html)})]), a.json)

    if a.cmd == "gate":
        from .core import gates
        if a.gate_cmd == "ui":
            from .core.gateui import serve
            return serve(store, open_browser=not a.no_open, port=a.port)
        if a.gate_cmd == "list":
            rows = gates.list_gates(store)
            if a.json:
                print(json.dumps(rows, indent=2, default=str)); return 0
            if not rows:
                print("No open gates."); return 0
            print(f"{len(rows)} gate(s) awaiting a human:\n")
            for j in rows:
                p = j["payload"]
                print(f"  {j['token']}  [{p.get('gate_type','?')}]  {j['source']}")
                print(f"    {p.get('url','')}")
                if p.get("capture"):
                    print(f"    capture: {', '.join(p['capture'])}")
            return 4
        res, err = gates.resume(store, a.token, a.file or [])
        if err:
            print(f"ERROR: {err}"); return 5
        return _emit(res, a.json)

    if a.cmd == "extract":
        from pathlib import Path as _P
        from .core import extract as _x
        from .core.http import fetch as _fetch
        from .core.results import Hit as _Hit, Result as _R, Coverage as _C

        _pdf_provenance = None
        is_url = a.target.startswith(("http://", "https://"))
        if is_url:
            if a.browser:
                from .core import browser as _b
                raw, out = _b.fetch(a.target, source="extract", query=a.target, store=store)
            else:
                raw, out = _fetch(a.target, source="docs", query=a.target)
            if out:
                return _emit(out, a.json)
        else:
            _path = _P(a.target).expanduser()
            _head = b""
            try:
                with open(_path, "rb") as _fh:
                    _head = _fh.read(5)
            except OSError:
                pass
            # A PDF read as text is binary noise -- `--text` on a 2.4MB PDF
            # once reported "-242.3% reduction", i.e. the token lever running
            # backwards. Decode it properly instead.
            #
            # Text layer first: exact, fast, lossless. OCR ONLY when there is
            # no text layer to lose, because OCR is lossy in the one way that
            # matters on this beat -- it confuses 0/O, 1/l, 5/S and drops
            # digits in tables, and a wrong docket number archived with a
            # SHA-256 looks authoritative.
            if _head.startswith(b"%PDF"):
                from .core import pdf as _pdf
                try:
                    raw, _prov = _pdf.read(_path, allow_ocr=not a.no_ocr,
                                           max_pages=a.ocr_pages)
                except _pdf.PdfToolMissing as e:
                    from .core.results import AccessBlocker as _AB, Blocker as _B, Coverage as _Cv
                    return _emit(_AB(
                        query=a.target,
                        coverage=_Cv(queried=["extract"], errored={"extract": "missing-tool"}),
                        mechanism=_B.SERVER_ERROR, url=a.target, detail=str(e)), a.json)

                if _prov["source"] == "none":
                    from .core.results import AccessBlocker as _AB, Blocker as _B, Coverage as _Cv
                    return _emit(_AB(
                        query=a.target,
                        coverage=_Cv(queried=["extract"], errored={"extract": "no-text-layer"}),
                        mechanism=_B.SERVER_ERROR, url=a.target,
                        detail=(f"Scanned PDF: only {_prov['text_layer_chars']} characters of "
                                "text layer. OCR was disabled (--no-ocr). Re-run without it "
                                "to read this, and treat the result as unverified.")), a.json)

                if _prov["source"] == "ocr":
                    # Loud, on stderr, every time. An OCR read that a worker
                    # mistakes for verbatim text is the failure this guards.
                    print(f"cascade-search: NO TEXT LAYER "
                          f"({_prov['text_layer_chars']} chars) -- fell back to OCR. "
                          f"{_prov['pages_ocred']} page(s) at {_prov['dpi']}dpi"
                          + (f", TRUNCATED at the {_prov['page_cap']}-page cap"
                             if _prov.get("truncated") else "")
                          + ".\n  This text is NOT verbatim and has NOT been human-verified. "
                            "Confirm every identifier, docket number and dollar figure against "
                            "the page image before citing.", file=sys.stderr)
                _pdf_provenance = _prov
            else:
                raw = _path.read_text(errors="replace")
        is_html = is_url or a.target.endswith((".html", ".htm"))

        payload = {}
        if a.grep:
            payload["matches"] = _x.grep(raw, a.grep, is_html=is_html)
        if a.ids:
            payload["identifiers"] = _x.identifiers(raw, is_html=is_html)
        if a.tables:
            payload["tables"] = _x.tables(raw) if is_html else []
        if a.text or not payload:
            payload["text"] = _x.page_text(raw, a.max_chars) if is_html else raw[:a.max_chars or None]

        if _pdf_provenance:
            payload["provenance"] = _pdf_provenance
        # For a PDF the honest baseline is the DECODED text, not the file's
        # bytes. Comparing against the binary produced nonsense like
        # "-285.3% reduction" on an OCR read that had in fact done its job.
        stats = _x.savings(raw, json.dumps(payload, default=str))
        if a.json:
            print(json.dumps({"target": a.target, **payload, "savings": stats}, indent=2, default=str))
        else:
            print(f"== Extract == {a.target}")
            if _pdf_provenance:
                src = _pdf_provenance["source"]
                if src == "ocr":
                    print(f"source:   OCR ({_pdf_provenance['pages_ocred']} pages) "
                          f"-- NOT verbatim, NOT human-verified")
                else:
                    print(f"source:   PDF text layer ({_pdf_provenance.get('chars', 0):,} chars, exact)")
            _delta = (f"{stats['reduction_pct']}% reduction" if not stats.get("expanded")
                      else f"EXPANDED by {stats['extracted_tokens_est'] - stats['raw_tokens_est']:,} tok "
                           f"-- the source was already small")
            print(f"raw ~{stats['raw_tokens_est']:,} tok -> extracted ~{stats['extracted_tokens_est']:,} tok "
                  f"({_delta})\n")
            for m in payload.get("matches", []):
                print(f"  [{m['pattern']}] ...{m['context']}...")
            if payload.get("identifiers") and (_pdf_provenance or {}).get("ocr"):
                # Identifiers are precisely what OCR corrupts -- 0/O, 1/l, 5/S,
                # and dropped digits in tables. Extracting them from OCR'd text
                # without saying so hands a worker a citable-looking string that
                # may be one character wrong.
                print("  !! identifiers below came from OCR -- verify each against "
                      "the page image before citing")
            for k, v in (payload.get("identifiers") or {}).items():
                print(f"  {k:14} {', '.join(v[:10])}")
            for i, t in enumerate(payload.get("tables", [])[:5], 1):
                print(f"  table {i}: {len(t)} rows | {' | '.join(t[0][:5])}")
            if "text" in payload:
                print(payload["text"][:4000])
        return 0

    if a.cmd == "record":
        # Results carry a curated view; this is the escape hatch to everything
        # the source actually sent. Storing all of it but returning only what
        # is generally useful keeps the common call cheap without losing the
        # field that turns out to matter on one particular case.
        if a.list or not a.record_id:
            rows = store.find_records(source=a.source, query=a.query)
            if a.json:
                print(json.dumps(rows, indent=2, default=str)); return 0
            if not rows:
                print("No stored records. They are written as searches run."); return 1
            print(f"{len(rows)} record(s):\n")
            for r in rows:
                print(f"  {r['record_id']:44} {r['source']:16} {r['query'][:40]}")
            return 0

        rec = store.get_record(a.record_id)
        if not rec:
            print(f"No record {a.record_id!r}. List with: cascade-search record --list",
                  file=sys.stderr)
            return 1
        payload = rec["payload"]
        if a.fields:
            want = [f.strip() for f in a.fields.split(",") if f.strip()]
            payload = {k: v for k, v in payload.items() if k in want}
        if a.json:
            print(json.dumps({**rec, "payload": payload}, indent=2, default=str))
        else:
            print(f"== Record == {rec['record_id']}")
            print(f"source: {rec['source']}   query: {rec['query']}\n")
            for k, v in sorted(payload.items()):
                if v in (None, "", [], {}):
                    continue
                print(f"  {k:26} {str(v)[:150]}")
        return 0

    if a.cmd == "limits":
        from .core.limits import POLICIES
        rows = []
        for name, pol in sorted(POLICIES.items()):
            used = [f"{store.count_calls(name, w)}/{m} per {int(w)}s" for w, m in pol.windows]
            rows.append({"source": name, "windows": used, "cacheable": pol.cacheable,
                         "index_origin": pol.index_origin, "note": pol.note})
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"\n{r['source']}")
                print(f"  usage:     {', '.join(r['windows'])}")
                print(f"  cacheable: {r['cacheable']}   index: {r['index_origin']}")
                if r["note"]:
                    print(f"  note:      {r['note']}")
        return 0
    return 5


if __name__ == "__main__":
    sys.exit(main())
