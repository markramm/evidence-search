"""evidence-search CLI.

Primary interface for Claude Code workers: Bash-native, pipeable, and it costs
no context until invoked. An MCP shim over the same core is Phase 2.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace

from .core.results import AccessBlocker, AwaitingHuman, Hit, RateLimited, VerifiedAbsence
from .core.store import Store
from .core.limits import Limiter


# Exit codes.
#
# Outcomes live at 10+ and NOTHING ELSE DOES. This is deliberate and was a bug
# fix, not a preference: VerifiedAbsence used to be exit 1, which is also what
# Python returns on any uncaught exception, what argparse returns on a bad flag,
# and the universal shell convention for "something went wrong." The README's
# own example branched on `$? -eq 1` and labelled that branch "publishable" --
# so a crash inside a fetch loop would have read to a shell caller as a
# certified negative finding. The single claim this tool exists to make is the
# one the old mapping was most likely to fake.
#
# Errors keep the conventional low codes so they behave like every other program:
#   1  generic failure / uncaught exception (Python's default; we never return it
#      deliberately, which is the point -- if you see 1, something broke)
#   2  usage error (argparse's default; bad flags, missing required args)
#   3  no local data (a `record`/`gate` listing with nothing stored -- an empty
#      shelf in OUR store, never a claim about the world)
EXIT_USAGE = 2
EXIT_NO_LOCAL_DATA = 3
EXIT_INTERNAL_ERROR = 70  # sysexits.h EX_SOFTWARE

EXIT_HIT = 10
EXIT_VERIFIED_ABSENCE = 11
EXIT_ACCESS_BLOCKER = 12
EXIT_RATE_LIMITED = 13
EXIT_AWAITING_HUMAN = 14

EXIT_BY_OUTCOME = {
    Hit: EXIT_HIT,
    VerifiedAbsence: EXIT_VERIFIED_ABSENCE,
    AccessBlocker: EXIT_ACCESS_BLOCKER,
    RateLimited: EXIT_RATE_LIMITED,
    AwaitingHuman: EXIT_AWAITING_HUMAN,
}


def _asked_strings(outcome) -> list[str]:
    """The exact query strings sent, one line per distinct (source, query, params).

    Deduped, because five probes against one string is one specification, not
    five. Params are included because they scope the question as much as the
    query does -- `Frazier` in Caddo County in 2013 is not `Frazier` statewide.
    """
    seen: list[str] = []
    for pr in getattr(outcome, "probes", []) or []:
        q = (pr.query or "").strip()
        if not q:
            continue
        scope = ", ".join(f"{k}={v}" for k, v in sorted((pr.params or {}).items()) if v not in (None, ""))
        line = f"{q!r}" + (f"   [{scope}]" if scope else "")
        line = f"{line}   via {pr.source}"
        if pr.exact_match_supported is False:
            line += "   (FUZZY -- endpoint does not honour exact-phrase matching)"
        if line not in seen:
            seen.append(line)
    return seen


def _emit(outcome, as_json: bool, limit: int | None = None) -> int:
    """Print, and return an exit code that encodes the OUTCOME TYPE.

    Exit codes let a shell caller branch without parsing:
      10 hit | 11 verified absence | 12 blocked | 13 rate-limited | 14 awaiting human

    Anything below 10 means the tool failed, not that the world is empty. See
    the EXIT_* block above for why absence is not 1.

    `limit` trims the DISPLAY only. Sources that page upstream (usaspending,
    crossref, fedreg) apply their own limit at the API; the rest return what one
    call returns, and this caps the rows printed so `--limit` means the same
    thing everywhere. Trimming display is safe here precisely because the header
    reports the true total -- a shortened list can never read as a smaller
    corpus.
    """
    if limit is not None and isinstance(outcome, Hit) and limit < len(outcome.results):
        shown = list(outcome.results[:limit])
        if not any(r.meta.get("total_matches") is not None for r in shown):
            # Preserve the honest denominator when the trim would hide it.
            shown = [replace(shown[0], meta={**shown[0].meta,
                                             "total_matches": len(outcome.results)})] + shown[1:]
        outcome = replace(outcome, results=shown)
    if as_json:
        print(json.dumps(outcome.to_dict(), indent=2, default=str))
    else:
        kind = type(outcome).__name__
        print(f"== {kind} ==")
        print(f"query:    {outcome.query}")
        print(f"coverage: {outcome.coverage.summary()}")
        if isinstance(outcome, Hit):
            # The upstream TOTAL, when the source reported one.
            #
            # It was always carried on meta.total_matches, but only reachable via
            # --json, nested inside results[0] rather than at the top level. The
            # skill doc's most-repeated numeric caution is "read total_matches;
            # 'at least 20' is almost never the honest answer" -- and following
            # that advice meant writing a recursive JSON walker. One worker did
            # exactly that to learn a 20-row page stood for 293 matches. A count
            # that decides whether scale IS the claim should not be the hardest
            # field in the tool to reach.
            total = next((r.meta.get("total_matches") for r in outcome.results
                          if r.meta.get("total_matches") is not None), None)
            if total is not None and total > len(outcome.results):
                print(f"results:  {len(outcome.results)} of {total} total_matches"
                      f"  (this page only -- cite {total}, not {len(outcome.results)})\n")
            else:
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
                if r.meta.get("entity_caveat"):
                    print(f"     !! {r.meta['entity_caveat']}")
        elif isinstance(outcome, VerifiedAbsence):
            # THE EXACT STRINGS ASKED, first and loudest.
            #
            # Typed outcomes make execution auditable; they do nothing for
            # SPECIFICATION. The tool will certify a flawlessly-executed search
            # for the wrong string, and an absence on --lname Frazier is an
            # absence of a STRING: not Frasier, not a married name, not a
            # hyphenation, not a data-entry variant. The origin bug had two
            # halves -- searching the wrong corpora, and searching "watch
            # network" instead of the movement's own name. Typed outcomes fixed
            # the first half completely and the second not at all.
            #
            # So the query outranks the verdict in the display. What was asked
            # has to be as easy to challenge as whether it was asked properly.
            asked = _asked_strings(outcome)
            if asked:
                print("ASKED:")
                for line in asked:
                    print(f"  {line}")
                print("  ^ this is an absence OF THESE STRINGS. A variant spelling,")
                print("    married name, or transliteration is a DIFFERENT question.")
                print()
            print(f"searched: {outcome.searched}")
            for pr in outcome.probes:
                print(f"  probe:  {pr.describe()}")
                if pr.corpus:
                    print(f"          corpus: {pr.corpus}")
            if outcome.not_searched:
                print(f"NOT searched: {', '.join(outcome.not_searched)}")
            for c in outcome.caveats:
                print(f"  !! {c}")
            print()
            if outcome.is_absolute:
                print("PUBLISHABLE NEGATIVE: every endpoint exact-matched, nothing left")
                print("unsearched, no caveats. This is a finding, not a failure.")
            else:
                # Almost always this branch, deliberately. Proving a negative in
                # absolute terms is usually impossible; what is provable is the
                # bounded claim, and a reporter needs the bound stated.
                print("BOUNDED NEGATIVE -- publishable AS SCOPED, not as an absolute:")
                print(f"  {outcome.claim()}")
        elif isinstance(outcome, AccessBlocker):
            print(f"mechanism: {outcome.mechanism.value}")
            print(f"url:       {outcome.url}")
            print(f"detail:    {outcome.detail}")
            if outcome.is_wall:
                print("\nNOT a negative finding. Access was blocked.")
                if outcome.escalate_to_browser:
                    print("ESCALATABLE: a real browser session could plausibly pass this gate.")
            else:
                # 404/410. Nobody is walling us; the path is wrong or the id was
                # never issued. Saying "access was blocked" here invites an
                # overclaim about suppression -- the exact error this tool exists
                # to prevent, arriving from inside the tool.
                print("\nNOT a negative finding, and NOT a block -- the URL does not exist.")
                print("Check the path (e.g. /about vs /about-us). On an enumerated id range,")
                print("a 404 usually means that id was never issued or has been pruned.")
        elif isinstance(outcome, RateLimited):
            print(f"source:    {outcome.source}")
            # "retry after: Nones" when the source gave no interval -- say what
            # is actually true instead of appending a unit to a null.
            print(f"retry after: {outcome.retry_after_s}s" if outcome.retry_after_s
                  else "retry after: unspecified by the source")
            print(f"detail:    {outcome.detail}")
            print("\nTOOLING-LIMITED NEGATIVE -- explicitly NOT content-exhausted. Retry.")
        elif isinstance(outcome, AwaitingHuman):
            print(f"gate:  {outcome.gate_type.value}")
            print(f"url:   {outcome.url}")
            print(f"token: {outcome.resume_token}")
            print(f"\n{outcome.instructions}")
    return EXIT_BY_OUTCOME.get(type(outcome), EXIT_INTERNAL_ERROR)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evidence-search",
                                description="Federated research client with typed outcomes.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--wait", action="store_true",
                   help="block for short spacing waits instead of returning RateLimited "
                        "(sub-minute only; never waits out a real budget window)")
    sub = p.add_subparsers(dest="cmd", required=True)

    # Every source that returns a list takes --limit, spelled the same way.
    # Learning the flag six times (--rows, --per-page, --limit, or nothing at
    # all) cost a round-trip per source; the argparse error names the valid
    # flags but only after the call has already failed.
    def _limit(sp, default=25):
        sp.add_argument("--limit", type=int, default=default,
                        help="max results shown (the header still reports the true total)")

    n = sub.add_parser("news", help="Google News RSS (free, keyless)")
    n.add_argument("query"); _limit(n)

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
    c.add_argument("--format", choices=["rich", "json"], default="rich",
                   help="json is equivalent to the global --json")
    c.add_argument("--cursor",
                   help="next page: pass meta.next_cursor from a prior result "
                        "(v4 pages by cursor, not page number)")
    _limit(c)

    pp = sub.add_parser("propublica", help="ProPublica Trump-team financial disclosures")
    pp.add_argument("query"); _limit(pp)

    w = sub.add_parser("web", help="general web via a local SearXNG container")
    w.add_argument("query"); _limit(w)
    w.add_argument("--categories", default="general",
                   help="general, news, science, files, images…")
    w.add_argument("--page", type=int, default=1)
    w.add_argument("--base", help="instance URL (default $SEARXNG_URL or 127.0.0.1:8888)")

    us = sub.add_parser("usaspending", help="federal awards (no key; real counts)")
    us.add_argument("query", nargs="?", default=None,
                    help="recipient name (omit when using --detail)")
    us.add_argument("--detail", metavar="RECORD_ID",
                    help="full FPDS detail for one award (PSC, NAICS, competition) -- "
                         "pass meta.record_id or the bare numeric id")
    us.add_argument("--sum", action="store_true", dest="do_sum",
                    help="total Award Amount across pages; says whether it is complete "
                         "or a floor")
    us.add_argument("--max-pages", type=int, default=10,
                    help="page cap for --sum (100 awards per page)")
    us.add_argument("--count", action="store_true",
                    help="return the REAL total by award type instead of a page of awards")
    us.add_argument("--keywords", action="store_true",
                    help="full-text keyword search (FUZZY) instead of recipient-name match")
    us.add_argument("--all-types", action="store_true",
                    help="COUNT only: include every award group in the total")
    us.add_argument("--group",
                    choices=["contracts", "idvs", "grants", "loans",
                             "direct_payments", "other_assistance"],
                    default="contracts",
                    help="which award group to LIST (the API rejects a mixed list)")
    us.add_argument("--from", dest="date_from", help="award start on/after YYYY-MM-DD")
    us.add_argument("--to", dest="date_to", help="award start on/before YYYY-MM-DD")
    us.add_argument("--page", type=int, default=1)
    us.add_argument("--limit", type=int, default=25)

    cr = sub.add_parser("crossref", help="scholarly records; DOI lookup is exact")
    cr.add_argument("query", help="a DOI (exact) or title words (FUZZY discovery)")
    # --limit is the portable spelling across every source; --rows is kept
    # because it is upstream's own name and is in field commands already.
    cr.add_argument("--limit", "--rows", type=int, default=10, dest="rows",
                    help="max results (alias: --rows)")

    fr = sub.add_parser("fedreg", help="Federal Register rules/notices (real counts)")
    fr.add_argument("query")
    fr.add_argument("--type", dest="doc_type",
                    choices=["rule", "proposed", "notice", "presidential"],
                    help="filter by document type")
    fr.add_argument("--agency", help="agency slug, e.g. homeland-security-department")
    fr.add_argument("--from", dest="date_from", help="published on/after YYYY-MM-DD")
    fr.add_argument("--to", dest="date_to", help="published on/before YYYY-MM-DD")
    fr.add_argument("--page", type=int, default=1)
    fr.add_argument("--limit", "--per-page", type=int, default=20, dest="per_page",
                    help="max results per page (alias: --per-page)")

    fc = sub.add_parser("fec", help="FEC campaign finance (bulk-first, local index)")
    fc.add_argument("--from-committee", dest="from_committee", help="giving committee ID, e.g. C00370643")
    fc.add_argument("--to-committee", dest="to_committee", help="receiving committee ID")
    fc.add_argument("--committee", help="look up committee ID(s) by name (warns on multiple matches)")
    fc.add_argument("--cycles", help="FEC cycle or range, e.g. '2008' or '2006-2014' (required)")
    fc.add_argument("--cache-dir", help="local bulk-file cache (default ~/.evidence-search/fec_bulk)")
    _limit(fc)

    ld = sub.add_parser("lda", help="Senate LDA lobbying filings (LD-2; no key)")
    ld.add_argument("--client", help="client name (case-insensitive substring match)")
    ld.add_argument("--registrant", help="registrant/lobbying firm name (substring match)")
    ld.add_argument("--year", action="append", type=int,
                    help="filing year; repeat for multiple years (e.g. --year 2008 --year 2009)")
    ld.add_argument("--bill", help='bill number to find named in a filing, e.g. '
                                   '"H.R. 2994" or "S. 2160" (spacing/periods normalized)')
    _limit(ld)

    dc = sub.add_parser("docs", help="search a documentation site's index (no key)")
    dc.add_argument("query")
    dc.add_argument("--site", default="claude-code")
    dc.add_argument("--fetch-top", type=int, default=0, help="also fetch N page bodies")
    _limit(dc)

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
    sub.add_parser("hosts", help="show the browser-tier public-records allow-list")

    # Global flags must PRECEDE the subcommand, and argparse's bare
    # "unrecognized arguments: --json" gives no hint which way to move it.
    # Accept the other order rather than making the caller find out.
    _argv = list(sys.argv[1:] if argv is None else argv)
    for _g in ("--json", "--no-cache", "--wait"):
        if _g in _argv and _argv.index(_g) > 0:
            _argv.remove(_g)
            _argv.insert(0, _g)
    a = p.parse_args(_argv)
    store = Store(); limiter = Limiter(store); use_cache = not a.no_cache
    if getattr(a, "wait", False):
        limiter.wait_for_spacing = True

    if a.cmd == "news":
        from .engines.news_rss import search
        return _emit(search(a.query, store, limiter, use_cache), a.json, a.limit)

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
            print("--county is required (or use --list-counties)", file=sys.stderr); return EXIT_USAGE
        return _emit(oscn.search(a.county, a.lname or "", a.fname or "",
                                 a.year, store, limiter, use_cache), a.json)

    if a.cmd == "courtlistener":
        from .sources import courtlistener
        return _emit(courtlistener.search(a.query, a.type, a.court, store, limiter,
                                          use_cache, cursor=a.cursor),
                     a.json or a.format == "json", a.limit)

    if a.cmd == "propublica":
        from .sources import propublica_disclosures
        return _emit(propublica_disclosures.search(a.query, store, limiter, use_cache),
                     a.json, a.limit)

    if a.cmd == "web":
        from .engines.searxng import search as web_search
        return _emit(web_search(a.query, a.categories, a.page, a.base,
                                store, limiter, use_cache), a.json, a.limit)

    if a.cmd == "usaspending":
        from .sources import usaspending as usa
        from .core.results import Hit as _Hit_t
        if not a.query and not a.detail:
            print("usaspending needs a recipient name (or --detail <award-id>)",
                  file=sys.stderr)
            return EXIT_USAGE
        if a.detail:
            # The positional query is meaningless here -- detail is keyed on the
            # award id. Silently ignoring it meant a worker who mistyped a vendor
            # name got ANOTHER VENDOR'S record with no warning, which on this
            # beat is attributing one company's contract to another.
            out = usa.detail(a.detail, store, limiter)
            if a.query and isinstance(out, _Hit_t):
                got = (out.results[0].meta.get("recipient") or "")
                if a.query.strip().lower() not in got.lower():
                    print(f"evidence-search: NOTE -- you passed query {a.query!r}, but "
                          f"--detail is keyed on the award id and returned "
                          f"{got!r}. The query was NOT used to select this record.",
                          file=sys.stderr)
            return _emit(out, a.json)
        if a.do_sum:
            return _emit(usa.dollar_sum(a.query, by_recipient=not a.keywords,
                                        award_types=usa.AWARD_GROUPS[a.group],
                                        date_from=a.date_from, date_to=a.date_to,
                                        max_pages=a.max_pages, store=store,
                                        limiter=limiter), a.json)
        if a.count:
            types = usa.ALL_AWARD_TYPES if a.all_types else usa.CONTRACT_TYPES
            return _emit(usa.counts(a.query, by_recipient=not a.keywords,
                                    award_types=types, date_from=a.date_from,
                                    date_to=a.date_to, store=store, limiter=limiter,
                                    use_cache=use_cache), a.json)
        types = usa.AWARD_GROUPS[a.group]
        return _emit(usa.search(a.query, by_recipient=not a.keywords, award_types=types,
                                date_from=a.date_from, date_to=a.date_to,
                                limit=a.limit, page=a.page, store=store,
                                limiter=limiter, use_cache=use_cache), a.json)

    if a.cmd == "fec":
        from pathlib import Path as _Path
        from .sources import fec as fec_src
        if not a.cycles:
            print("fec needs --cycles, e.g. --cycles 2006-2014", file=sys.stderr)
            return EXIT_USAGE
        try:
            c_from, c_to = fec_src.parse_cycle_range(a.cycles)
        except ValueError as e:
            print(f"fec: {e}", file=sys.stderr)
            return EXIT_USAGE
        cache_dir = _Path(a.cache_dir).expanduser() if a.cache_dir else None
        if a.committee:
            return _emit(fec_src.lookup_committee(a.committee, c_from, c_to, store=store,
                                                  limiter=limiter, cache_dir=cache_dir,
                                                  use_cache=use_cache), a.json, a.limit)
        if not a.from_committee or not a.to_committee:
            print("fec needs --from-committee and --to-committee (or --committee to "
                  "look up an ID)", file=sys.stderr)
            return EXIT_USAGE
        return _emit(fec_src.between(a.from_committee, a.to_committee, c_from, c_to,
                                     store=store, limiter=limiter, cache_dir=cache_dir,
                                     use_cache=use_cache), a.json, a.limit)

    if a.cmd == "lda":
        from .sources import lda as lda_src
        if not a.client and not a.registrant:
            print("lda needs --client and/or --registrant", file=sys.stderr)
            return EXIT_USAGE
        return _emit(lda_src.search(client_name=a.client, registrant_name=a.registrant,
                                    years=a.year, bill=a.bill, store=store,
                                    limiter=limiter, use_cache=use_cache),
                     a.json, a.limit)

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
        return _emit(docs_src.search(a.query, a.site, a.fetch_top, store, limiter, use_cache),
                     a.json, a.limit)

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
            rec = _archive(html.encode(), _P(a.save).name, a.url, "evidence-search:browser")
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
            # Open gates ARE awaiting human action -- same outcome, same code.
            return EXIT_AWAITING_HUMAN
        res, err = gates.resume(store, a.token, a.file or [])
        if err:
            print(f"ERROR: {err}"); return EXIT_INTERNAL_ERROR
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

            # A REMOTE pdf was never decoded: the guard lived only on the local
            # branch, so `extract <pdf-url>` grepped raw binary, matched nothing,
            # and reported "100.0% reduction" -- a FALSE ABSENCE in the tool
            # built to prevent them. Same file by local path found the passage.
            if raw and raw.lstrip()[:5].startswith("%PDF"):
                import tempfile as _tf
                from .core import pdf as _pdf
                # Re-fetch as BYTES. `fetch` decoded the response as text, and
                # re-encoding that string corrupts the PDF -- enough that a file
                # with a perfect text layer fell back to OCR. The bytes have to
                # come off the wire undecoded.
                _bin, _berr = _fetch(a.target, source="docs", query=a.target, binary=True)
                if _berr:
                    return _emit(_berr, a.json)
                with _tf.NamedTemporaryFile(suffix=".pdf", delete=False) as _tmp:
                    _tmp.write(_bin)
                    _tmp_path = _P(_tmp.name)
                try:
                    raw, _prov = _pdf.read(_tmp_path, allow_ocr=not a.no_ocr,
                                           max_pages=a.ocr_pages)
                    if _prov["source"] == "ocr":
                        print(f"evidence-search: remote PDF had NO TEXT LAYER "
                              f"({_prov['text_layer_chars']} chars) -- fell back to OCR, "
                              f"{_prov['pages_ocred']} page(s). NOT verbatim, NOT "
                              "human-verified.", file=sys.stderr)
                    _pdf_provenance = _prov
                finally:
                    _tmp_path.unlink(missing_ok=True)
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
                    print(f"evidence-search: NO TEXT LAYER "
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
            if "matches" in payload and not [m for m in payload["matches"]
                                             if m["pattern"] != "__truncated__"]:
                # "100.0% reduction" over zero matches reads as success and means
                # the opposite. Reported twice by workers; it nearly produced a
                # wrong conclusion on an attorney-of-record question.
                pats = ", ".join(repr(x) for x in (a.grep or []))
                print(f"  NO MATCHES for {pats} in {stats['raw_tokens_est']:,} tokens "
                      "of text.\n  This is an absence IN THIS DOCUMENT ONLY -- the "
                      "document was read, the terms are not in it.")
            for m in payload.get("matches", []):
                if m["pattern"] == "__truncated__":
                    print(f"  !! {m['context']}")
                    continue
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
                # Truncating without a marker corrupted seven files for a worker
                # who only noticed when parsing failed -- silent data loss that
                # looks like success. Say what was cut, and offer the way out.
                txt = payload["text"]
                cap = a.max_chars or 4000
                if len(txt) > cap:
                    print(txt[:cap])
                    print(f"\n[TRUNCATED for display: {len(txt):,} chars total, "
                          f"{len(txt) - cap:,} not shown. Use --json for the full text, "
                          f"or --max-chars N to raise this cap.]")
                else:
                    print(txt)
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
                print("No stored records. They are written as searches run."); return EXIT_NO_LOCAL_DATA
            print(f"{len(rows)} record(s):\n")
            for r in rows:
                print(f"  {r['record_id']:44} {r['source']:16} {r['query'][:40]}")
            return 0

        rec = store.get_record(a.record_id)
        if not rec:
            print(f"No record {a.record_id!r}. List with: evidence-search record --list",
                  file=sys.stderr)
            return EXIT_NO_LOCAL_DATA
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
    if a.cmd == "hosts":
        # A worker who hits `host-not-allowed` should be able to SEE the
        # boundary rather than guess at it. Asked for in the field twice.
        from .core.browser import ALLOWED_HOSTS, load_user_hosts
        user = load_user_hosts()
        if a.json:
            print(json.dumps({"shipped": ALLOWED_HOSTS, "user": user}, indent=2))
        else:
            print("== Browser-tier allow-list (public records only) ==\n")
            for host, why in sorted(ALLOWED_HOSTS.items()):
                print(f"  {host:<32} {why}")
            if user:
                print("\n  -- local additions --")
                for host, why in sorted(user.items()):
                    print(f"  {host:<32} {why}")
            else:
                print("\n  No local additions. To add one (public records only --"
                      "\n  never a paywall, an auth boundary, or personal data):"
                      "\n    ~/.evidence-search/allowed_hosts.json"
                      '\n    {"sos.example.gov": "State SOS -- public business registry"}')
        return 0
    # Unreachable in practice -- argparse rejects unknown commands first.
    return EXIT_USAGE


def cli() -> int:
    """Console entry point.

    Wraps main() so an uncaught exception exits EX_SOFTWARE (70) rather than
    Python's default 1. Nothing in the outcome range can be produced by a crash:
    outcomes are 10+, and 70 is not one of them. A caller branching on
    "is this a finding?" cannot be handed a traceback that looks like one.
    """
    try:
        return main()
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130  # conventional: 128 + SIGINT
    except Exception as exc:  # noqa: BLE001 -- deliberate top-level guard
        print(f"evidence-search: internal error: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        print("This is a TOOL FAILURE, not a finding about the world.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    sys.exit(cli())
