"""evidence-search CLI.

Primary interface for Claude Code workers: Bash-native, pipeable, and it costs
no context until invoked. An MCP shim over the same core is Phase 2.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace

from . import __version__
from .core.results import AccessBlocker, AwaitingHuman, Hit, RateLimited, VerifiedAbsence
from .core.store import Store
from .core.limits import Limiter


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
    """The exact query strings sent, one line per distinct (source, query, params)."""
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
    """Print, and return an exit code that encodes the OUTCOME TYPE."""
    if limit is not None and isinstance(outcome, Hit) and limit < len(outcome.results):
        shown = list(outcome.results[:limit])
        if not any(r.meta.get("total_matches") is not None for r in shown):
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
                print("\nNOT a negative finding, and NOT a block -- the URL does not exist.")
                print("Check the path (e.g. /about vs /about-us). On an enumerated id range,")
                print("a 404 usually means that id was never issued or has been pruned.")
        elif isinstance(outcome, RateLimited):
            print(f"source:    {outcome.source}")
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


EXTRACT_MIN_VISIBLE_CHARS = 200
FETCH_WAIT_S = 10.0


def _reserve_fetch(limiter, url: str, via_browser: bool = False):
    """Claim a ledger slot for a fetch of an arbitrary URL."""
    import math
    from .core.limits import bucket_for_url
    from .core.results import Coverage as _Cv
    bucket = bucket_for_url(url)
    ok, retry, why = limiter.reserve(bucket, wait_s=FETCH_WAIT_S,
                                     honor_backoff=not via_browser)
    if ok:
        return bucket, None
    return bucket, RateLimited(
        query=url, coverage=_Cv(queried=[bucket], rate_limited=[bucket]),
        source=bucket, retry_after_s=math.ceil(retry) if retry else None,
        detail=f"{why}. Nothing was fetched; `evidence-search limits` shows the state.")


def _read_pdf(path, target: str, allow_ocr: bool, max_pages: int, label: str = ""):
    """Decode a PDF for `extract`: (text, provenance, blocker_or_None)."""
    from .core import pdf as _pdf
    from .core.results import AccessBlocker as _AB, Blocker as _B, Coverage as _Cv

    def _blocker(tag: str, detail: str):
        return _AB(query=target, coverage=_Cv(queried=["extract"], errored={"extract": tag}),
                   mechanism=_B.SERVER_ERROR, url=target, detail=detail)

    try:
        raw, prov = _pdf.read(path, allow_ocr=allow_ocr, max_pages=max_pages)
    except _pdf.PdfToolMissing as e:
        return None, None, _blocker("missing-tool", str(e))
    except _pdf.PdfReadFailed as e:
        return None, None, _blocker("pdf-read-failed", str(e))

    if prov["source"] == "none":
        return None, None, _blocker(
            "no-text-layer",
            f"Scanned PDF: only {prov['text_layer_chars']} characters of "
            "text layer. OCR was disabled (--no-ocr). Re-run without it "
            "to read this, and treat the result as unverified.")

    if prov["source"] == "ocr" and not raw.strip():
        return None, None, _blocker(
            "no-text",
            f"No text layer ({prov['text_layer_chars']} chars) and OCR of "
            f"{prov['pages_ocred']} page(s) produced no text. The document was "
            "NOT read; this says nothing about what it contains.")

    if prov["source"] == "ocr":
        print(f"evidence-search: {label + ' had ' if label else ''}NO TEXT LAYER "
              f"({prov['text_layer_chars']} chars) -- fell back to OCR. "
              f"{prov['pages_ocred']} page(s) at {prov['dpi']}dpi"
              + (f", TRUNCATED at the {prov['page_cap']}-page cap"
                 if prov.get("truncated") else "")
              + ".\n  This text is NOT verbatim and has NOT been human-verified. "
                "Confirm every identifier, docket number and dollar figure against "
                "the page image before citing.", file=sys.stderr)
    return raw, prov, None


def _dispatch_source_cmd(a, store: Store, limiter: Limiter, use_cache: bool,
                         dynamic_plugins: dict[str, object]) -> int | None:
    """Dispatch built-in and dynamic source/plugin subcommands; returns exit code or None."""
    from pathlib import Path as _Path

    if a.cmd in dynamic_plugins:
        sp = dynamic_plugins[a.cmd]
        return _emit(sp.run(a.query, where=a.where, limit=a.limit or 20,
                            store=store, limiter=limiter, use_cache=use_cache),
                     a.json, a.limit)

    def _run_simple() -> int | None:
        cmd = a.cmd
        if cmd == "news":
            from .engines.news_rss import search
            return _emit(search(a.query, store, limiter, use_cache), a.json, a.limit)
        if cmd == "propublica":
            from .sources import propublica_disclosures
            return _emit(propublica_disclosures.search(a.query, store, limiter, use_cache), a.json, a.limit)
        if cmd == "web":
            from .engines.web import search as web_search
            return _emit(web_search(a.query, a.categories, a.page, a.base, store, limiter, use_cache,
                                    provider=a.provider, limit=a.limit,
                                    freshness=a.freshness, goggles=a.goggle), a.json, a.limit)
        if cmd == "crossref":
            from .sources import crossref as cr_src
            return _emit(cr_src.search(a.query, a.rows, store, limiter, use_cache), a.json)
        if cmd == "fedreg":
            from .sources import federal_register as fr_src
            return _emit(fr_src.search(a.query, a.doc_type, a.agency, a.date_from,
                                       a.date_to, a.per_page, a.page, store, limiter, use_cache), a.json)
        if cmd == "docs":
            from .sources import docs as docs_src
            return _emit(docs_src.search(a.query, a.site, a.fetch_top, store, limiter, use_cache),
                         a.json, a.limit)
        if cmd == "wayback":
            from .sources import wayback as wb_src
            return _emit(wb_src.search(a.url, match_type=a.match_type, date_from=a.date_from,
                                       date_to=a.date_to, status_code=a.status_code,
                                       collapse_digest=a.collapse_digest, limit=a.limit or 25,
                                       store=store, limiter=limiter, use_cache=use_cache), a.json, a.limit)
        if cmd == "socrata":
            from .sources import socrata as soc_src
            return _emit(soc_src.query(a.domain, a.dataset, a.query, where=a.where, select=a.select,
                                       order=a.order, limit=a.limit or 20, store=store,
                                       limiter=limiter, use_cache=use_cache), a.json, a.limit)
        if cmd == "arcgis":
            from .sources import arcgis as arc_src
            return _emit(arc_src.query(a.layer_url, a.query, field=a.field, where=a.where,
                                       out_fields=a.out_fields, limit=a.limit or 20, store=store,
                                       limiter=limiter, use_cache=use_cache), a.json, a.limit)
        if cmd == "kev":
            from .sources import kev as kev_src
            return _emit(kev_src.search(a.query, vendor=a.vendor, product=a.product, cve_id=a.cve_id,
                                        ransomware_only=a.ransomware_only, nvd=a.nvd,
                                        limit=a.limit or 25, store=store, limiter=limiter,
                                        use_cache=use_cache), a.json, a.limit)
        if cmd == "crtsh":
            from .sources import crtsh as crt_src
            return _emit(crt_src.search(a.domain, active_only=a.active_only, limit=a.limit or 50,
                                        store=store, limiter=limiter, use_cache=use_cache), a.json, a.limit)
        if cmd == "openfda":
            from .sources import openfda as fda_src
            return _emit(fda_src.search(a.query, endpoint=a.endpoint, firm=a.firm, limit=a.limit or 25,
                                        store=store, limiter=limiter, use_cache=use_cache), a.json, a.limit)
        if cmd == "cfpb":
            from .sources import cfpb as cfpb_src
            return _emit(cfpb_src.search(a.query, company=a.company, product=a.product, state=a.state,
                                         limit=a.limit or 25, store=store, limiter=limiter,
                                         use_cache=use_cache), a.json, a.limit)
        if cmd == "icij":
            from .sources import icij as icij_src
            cdir = _Path(a.cache_dir).expanduser() if a.cache_dir else None
            return _emit(icij_src.search(a.query, node_type=a.node_type, jurisdiction=a.jurisdiction,
                                         dataset=a.dataset, refresh=a.refresh, limit=a.limit or 25,
                                         store=store, limiter=limiter, cache_dir=cdir,
                                         use_cache=use_cache), a.json, a.limit)
        if cmd == "brokers":
            from .sources import brokers as brok_src
            cdir = _Path(a.cache_dir).expanduser() if a.cache_dir else None
            return _emit(brok_src.search(a.query, state=a.state, exact_only=a.exact,
                                         refresh=a.refresh, limit=a.limit or 25, store=store,
                                         limiter=limiter, cache_dir=cdir, use_cache=use_cache),
                         a.json, a.limit)
        if cmd == "gdelt":
            from .sources import gdelt as gd_src
            return _emit(gd_src.search(a.query, domain=a.domain, country=a.country, language=a.language,
                                       timespan=a.timespan, limit=a.limit or 25, store=store,
                                       limiter=limiter, use_cache=use_cache), a.json, a.limit)
        if cmd == "sanctions":
            from .sources import sanctions as sn_src
            lists = ("ofac", "eu") if a.list_choice == "all" else (a.list_choice,)
            cdir = _Path(a.cache_dir).expanduser() if a.cache_dir else None
            return _emit(sn_src.search(a.query, lists=lists, exact_only=a.exact, threshold=a.threshold,
                                       refresh=a.refresh, store=store, limiter=limiter,
                                       cache_dir=cdir, use_cache=use_cache), a.json, a.limit)
        return None

    simple_rc = _run_simple()
    if simple_rc is not None:
        return simple_rc

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
            print("--county is required (or use --list-counties)", file=sys.stderr)
            return EXIT_USAGE
        return _emit(oscn.search(a.county, a.lname or "", a.fname or "", a.year, store, limiter, use_cache), a.json)

    if a.cmd == "courtlistener":
        from .sources import courtlistener
        if a.docket:
            return _emit(courtlistener.docket(a.docket, since=a.since, grep_pattern=a.docket_grep,
                                              store=store, limiter=limiter, use_cache=use_cache),
                         a.json or a.format == "json", a.limit)
        if not a.query:
            print("courtlistener needs a query (or --docket <court>:<number>)", file=sys.stderr)
            return EXIT_USAGE
        return _emit(courtlistener.search(a.query, a.type, a.court, store, limiter, use_cache, cursor=a.cursor),
                     a.json or a.format == "json", a.limit)

    if a.cmd == "usaspending":
        from .sources import usaspending as usa
        from .core.results import Hit as _Hit_t
        if not a.query and not a.detail and not a.idv and not a.transactions:
            print("usaspending needs a recipient name (or --detail / --idv / --transactions)", file=sys.stderr)
            return EXIT_USAGE
        if a.detail:
            out = usa.detail(a.detail, store, limiter)
            if a.query and isinstance(out, _Hit_t):
                got = (out.results[0].meta.get("recipient") or "")
                if a.query.strip().lower() not in got.lower():
                    print(f"evidence-search: NOTE -- you passed query {a.query!r}, but "
                          f"--detail is keyed on the award id and returned "
                          f"{got!r}. The query was NOT used to select this record.",
                          file=sys.stderr)
            return _emit(out, a.json)
        if a.idv:
            return _emit(usa.idv_orders(a.idv, limit=a.limit, page=a.page, store=store,
                                        limiter=limiter, use_cache=use_cache), a.json)
        if a.transactions:
            return _emit(usa.transactions(a.transactions, limit=a.limit, page=a.page, store=store,
                                          limiter=limiter, use_cache=use_cache), a.json)
        if a.do_sum:
            types = (None if a.group == "contracts" and usa._piid_instrument(a.query) in usa._IDV_INSTRUMENTS
                     else usa.AWARD_GROUPS[a.group])
            return _emit(usa.dollar_sum(a.query, by_recipient=not a.keywords, award_types=types,
                                        date_from=a.date_from, date_to=a.date_to, agency=a.agency,
                                        max_pages=a.max_pages, store=store, limiter=limiter), a.json)
        if a.count:
            types = usa.ALL_AWARD_TYPES if a.all_types else usa.CONTRACT_TYPES
            return _emit(usa.counts(a.query, by_recipient=not a.keywords, award_types=types,
                                    date_from=a.date_from, date_to=a.date_to, agency=a.agency,
                                    store=store, limiter=limiter, use_cache=use_cache), a.json)
        types = (None if a.group == "contracts" and usa._piid_instrument(a.query) in usa._IDV_INSTRUMENTS
                 else usa.AWARD_GROUPS[a.group])
        return _emit(usa.search(a.query, by_recipient=not a.keywords, award_types=types,
                                date_from=a.date_from, date_to=a.date_to, agency=a.agency,
                                limit=a.limit, page=a.page, store=store,
                                limiter=limiter, use_cache=use_cache), a.json)

    if a.cmd == "fec":
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
            print("fec needs --from-committee and --to-committee (or --committee to look up an ID)", file=sys.stderr)
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
                                    limiter=limiter, use_cache=use_cache), a.json, a.limit)

    if a.cmd == "samgov":
        from .sources import samgov as sg_src
        if a.attachments:
            return _emit(sg_src.attachments(a.attachments, download=a.download, store=store,
                                            limiter=limiter, use_cache=use_cache), a.json, a.limit)
        if a.opp_id:
            return _emit(sg_src.detail(a.opp_id, store=store, limiter=limiter, use_cache=use_cache), a.json)
        if not a.query:
            print("samgov needs a query (or --detail <opp-id> / --attachments <opp-id>)", file=sys.stderr)
            return EXIT_USAGE
        return _emit(sg_src.search(a.query, active_only=a.active_only, notice_type=a.notice_type,
                                   limit=a.limit, page=a.page, store=store,
                                   limiter=limiter, use_cache=use_cache), a.json, a.limit)

    if a.cmd == "nonprofit":
        from .sources import nonprofit as np_src
        if a.ein:
            return _emit(np_src.by_ein(a.ein, year=a.year, store=store, limiter=limiter, use_cache=use_cache),
                         a.json, a.limit)
        if not a.query:
            print("nonprofit needs an organization name (or --ein <EIN>)", file=sys.stderr)
            return EXIT_USAGE
        return _emit(np_src.search(a.query, state=a.state, page=a.page, store=store,
                                   limiter=limiter, use_cache=use_cache), a.json, a.limit)

    if a.cmd == "sec":
        from .sources import sec as sec_src
        if a.company:
            return _emit(sec_src.submissions(a.company, forms=a.forms, limit=a.limit, store=store,
                                             limiter=limiter, use_cache=use_cache), a.json, a.limit)
        q = a.fulltext or a.query
        if not q:
            print("sec needs a query, --fulltext <query>, or --company <name|ticker|CIK>", file=sys.stderr)
            return EXIT_USAGE
        return _emit(sec_src.fulltext(q, forms=a.forms, date_from=a.date_from, date_to=a.date_to,
                                      entity=a.entity, page=a.page, limit=a.limit, store=store,
                                      limiter=limiter, use_cache=use_cache), a.json, a.limit)

    if a.cmd == "fara":
        from .sources import fara as fara_src
        if not any([a.query, a.principal, a.registrant, a.country, a.reg_number]):
            print("fara needs a query or at least one filter (--principal, --registrant, --country, --reg-number)",
                  file=sys.stderr)
            return EXIT_USAGE
        cache_dir = _Path(a.cache_dir).expanduser() if a.cache_dir else None
        return _emit(fara_src.search(a.query, principal=a.principal, registrant=a.registrant,
                                     country=a.country, reg_number=a.reg_number,
                                     active_only=a.active_only, refresh=a.refresh,
                                     limit=a.limit or 25, store=store, limiter=limiter,
                                     cache_dir=cache_dir, use_cache=use_cache), a.json, a.limit)

    if a.cmd == "openalex":
        from .sources import openalex as oa_src
        if not any([a.query, a.author, a.institution, a.funder, a.doi, a.year, a.retracted_only]):
            print("openalex needs a query or at least one filter (--author, --institution, --funder, --doi, --year, --retracted-only)",
                  file=sys.stderr)
            return EXIT_USAGE
        return _emit(oa_src.search(a.query, author=a.author, institution=a.institution,
                                   funder=a.funder, doi=a.doi, year=a.year,
                                   retracted_only=a.retracted_only, limit=a.limit or 25,
                                   store=store, limiter=limiter, use_cache=use_cache), a.json, a.limit)

    if a.cmd == "epa":
        from .sources import epa as epa_src
        if not any([a.query, a.state, a.zip_code]):
            print("epa needs a facility name query or --state / --zip", file=sys.stderr)
            return EXIT_USAGE
        return _emit(epa_src.search(a.query, state=a.state, zip_code=a.zip_code,
                                    violators_only=a.violators_only, limit=a.limit or 25,
                                    store=store, limiter=limiter, use_cache=use_cache), a.json, a.limit)

    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evidence-search",
                                description="Federated research client with typed outcomes.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--wait", action="store_true",
                   help="block for short spacing waits instead of returning RateLimited "
                        "(sub-minute only; never waits out a real budget window)")
    p.add_argument("--wait-max", type=float, default=0.0, metavar="SECONDS",
                   help="wait up to SECONDS for a slot (spacing, the per-minute "
                        "window, a shared 429 block or backoff) instead of returning "
                        "RateLimited; an hour/day window or session cap still "
                        "returns at once. Default 0: do not wait")
    sub = p.add_subparsers(dest="cmd", required=True)

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
    c.add_argument("query", nargs="?", default=None,
                   help='QUOTE phrases when counting: "Force Science '
                        'Institute" -> 155 matches, unquoted -> 51,622')
    c.add_argument("--type", default="r", choices=["r", "rd", "o", "p"])
    c.add_argument("--court")
    c.add_argument("--docket", metavar="COURT:NUMBER",
                   help="docket-watch mode (#18), e.g. dcd:1:26-cv-01352")
    c.add_argument("--since", help="--docket only: entries filed on/after YYYY-MM-DD")
    c.add_argument("--grep", dest="docket_grep",
                   help="--docket only: filter docket entries by regex")
    c.add_argument("--format", choices=["rich", "json"], default="rich",
                   help="json is equivalent to the global --json")
    c.add_argument("--cursor",
                   help="next page: pass meta.next_cursor from a prior result "
                        "(v4 pages by cursor, not page number)")
    _limit(c)

    pp = sub.add_parser("propublica", help="ProPublica Trump-team financial disclosures")
    pp.add_argument("query"); _limit(pp)

    w = sub.add_parser("web", help="general web search (zero-daemon Mwmbl default; optional API/SearXNG)")
    w.add_argument("query"); _limit(w)
    w.add_argument("--provider", default="auto",
                   choices=["auto", "mwmbl", "brave", "serper", "tavily", "searxng"],
                   help="search backend (default: auto-detect keys/SEARXNG_URL, else keyless Mwmbl)")
    w.add_argument("--categories", default="general",
                   help="general, news, science, files, images…")
    w.add_argument("--page", type=int, default=1)
    w.add_argument("--base", help="optional SearXNG instance URL (implies --provider searxng)")
    w.add_argument("--freshness", default=None,
                   help="Brave only: pd, pw, pm, py, or YYYY-MM-DDtoYYYY-MM-DD")
    w.add_argument("--goggle", action="append", default=None,
                   help="Brave only, repeatable up to 3: a goggle URL, inline rules, or a local .goggle file")

    us = sub.add_parser("usaspending", help="federal awards (no key; real counts)")
    us.add_argument("query", nargs="?", default=None,
                    help="recipient name (omit when using --detail, --idv, or --transactions)")
    us.add_argument("--detail", metavar="RECORD_ID",
                    help="full FPDS detail for one award (PSC, NAICS, competition) -- "
                         "pass meta.record_id or the bare numeric id")
    us.add_argument("--idv", metavar="PIID",
                    help="child delivery/task orders issued under an IDV/IDIQ (#17)")
    us.add_argument("--transactions", "--modifications", metavar="AWARD_ID",
                    dest="transactions",
                    help="per-award modification and transaction history")
    us.add_argument("--agency",
                    help="filter by awarding agency (e.g. ICE, CBP, DHS, DOD, or full name)")
    us.add_argument("--sum", action="store_true", dest="do_sum",
                    help="total Award Amount across pages; says whether it is complete "
                         "or a floor")
    us.add_argument("--max-pages", type=int, default=10,
                    help="page cap for --sum (100 awards per page)")
    us.add_argument("--count", action="store_true",
                    help="return the REAL total by award type instead of a page of awards")
    us.add_argument("--keywords", action="store_true",
                    help="full-text keyword search (FUZZY) instead of recipient-name match "
                         "(also the mode to use for award IDs and PIIDs)")
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

    sg = sub.add_parser("samgov", help="SAM.gov contract opportunities and public attachments (#2)")
    sg.add_argument("query", nargs="?", default=None,
                    help="keywords or quoted solicitation number")
    sg.add_argument("--detail", "--opp-id", dest="opp_id",
                    help="full notice narrative for one SAM.gov opportunity ID")
    sg.add_argument("--attachments", metavar="OPP_ID",
                    help="list public attachments for an opportunity ID")
    sg.add_argument("--download", action="store_true",
                    help="with --attachments: download and archive public files")
    sg.add_argument("--active-only", action="store_true",
                    help="only active opportunities")
    sg.add_argument("--type", dest="notice_type",
                    help="filter by notice type code")
    sg.add_argument("--page", type=int, default=1)
    _limit(sg)

    np_p = sub.add_parser("nonprofit", help="ProPublica Nonprofit Explorer IRS Form 990 search (#4)")
    np_p.add_argument("query", nargs="?", default=None,
                      help="organization name or EIN")
    np_p.add_argument("--ein", help="lookup organization and Form 990 filing history by EIN")
    np_p.add_argument("--year", type=int, help="tax year filter for --ein")
    np_p.add_argument("--state", help="two-letter state filter, e.g. VA")
    np_p.add_argument("--page", type=int, default=1)
    _limit(np_p)

    sc = sub.add_parser("sec", help="SEC EDGAR full-text search (EFTS) and company submissions (#1)")
    sc.add_argument("query", nargs="?", default=None,
                    help="full-text search query (or use --fulltext / --company)")
    sc.add_argument("--fulltext", help="search full text of SEC filings (2001+; quote phrases for exact match)")
    sc.add_argument("--company", "--cik", dest="company",
                    help="list company submissions by name, ticker, or 10-digit CIK")
    sc.add_argument("--forms", help="comma-separated form types, e.g. 10-K,10-Q,8-K")
    sc.add_argument("--entity", help="filter --fulltext to a CIK or entity name")
    sc.add_argument("--from", dest="date_from", help="filing date on/after YYYY-MM-DD")
    sc.add_argument("--to", dest="date_to", help="filing date on/before YYYY-MM-DD")
    sc.add_argument("--page", type=int, default=1)
    _limit(sc)

    sn = sub.add_parser("sanctions", help="OFAC SDN and EU Consolidated sanctions screening (#3)")
    sn.add_argument("query", help="entity or individual name to screen")
    sn.add_argument("--list", dest="list_choice", choices=["all", "ofac", "eu"], default="all")
    sn.add_argument("--exact", action="store_true",
                    help="exact normalized name/alias match only (disable fuzzy scoring)")
    sn.add_argument("--threshold", type=float, default=0.85,
                    help="similarity threshold for possible_match (default 0.85)")
    sn.add_argument("--refresh", action="store_true",
                    help="force re-download of bulk sanctions CSVs")
    sn.add_argument("--cache-dir", help="local bulk index cache directory")
    _limit(sn)

    fa = sub.add_parser("fara", help="DOJ Foreign Agents Registration Act (FARA) foreign principals and registrants")
    fa.add_argument("query", nargs="?", default="", help="search across foreign principal, registrant, country, or reg number")
    fa.add_argument("--principal", help="foreign principal name (substring match)")
    fa.add_argument("--registrant", help="U.S. registrant / lobbying firm name (substring match)")
    fa.add_argument("--country", help="foreign country / location represented")
    fa.add_argument("--reg-number", dest="reg_number", help="exact FARA registration number")
    fa.add_argument("--active-only", action="store_true", help="only currently active foreign-principal registrations")
    fa.add_argument("--refresh", action="store_true", help="force re-download of bulk FARA dataset")
    fa.add_argument("--cache-dir", help="local FARA bulk SQLite cache directory")
    _limit(fa)

    wb = sub.add_parser("wayback", help="Internet Archive Wayback Machine CDX historical URL capture index")
    wb.add_argument("url", help="target URL or domain to look up in the Wayback Machine")
    wb.add_argument("--match-type", dest="match_type", choices=["exact", "prefix", "host", "domain"],
                    default="exact", help="CDX URL matching mode (default: exact)")
    wb.add_argument("--from", dest="date_from", help="captures on/after YYYY-MM-DD or YYYYMMDD")
    wb.add_argument("--to", dest="date_to", help="captures on/before YYYY-MM-DD or YYYYMMDD")
    wb.add_argument("--status-code", dest="status_code", help="filter by HTTP status at capture time, e.g. 200")
    wb.add_argument("--collapse-digest", action="store_true",
                    help="deduplicate consecutive captures with identical SHA-1 content digest")
    _limit(wb)

    oa = sub.add_parser("openalex", help="OpenAlex scholarly works, authors, institutions, grants, and retractions")
    oa.add_argument("query", nargs="?", default="", help="title/abstract/fulltext keywords")
    oa.add_argument("--author", help="author name, ORCID (0000-...), or OpenAlex author ID")
    oa.add_argument("--institution", help="institution name, ROR URL, or OpenAlex institution ID")
    oa.add_argument("--funder", help="funding agency or grant funder name")
    oa.add_argument("--doi", help="exact DOI lookup")
    oa.add_argument("--year", help="publication year or range (e.g. 2024 or 2020-2025)")
    oa.add_argument("--retracted-only", action="store_true",
                    help="only return works flagged as retracted (Retraction Watch / OpenAlex)")
    _limit(oa)

    soc = sub.add_parser("socrata", help="Generic Socrata / Tyler SODA v2 open-data portal driver (state/city checkbooks)")
    soc.add_argument("query", nargs="?", default="", help="full-text search ($q) across the Socrata dataset")
    soc.add_argument("--domain", required=True, help="Socrata portal hostname, e.g. data.cityofnewyork.us")
    soc.add_argument("--dataset", required=True, help="Socrata 4x4 dataset identifier, e.g. abcd-1234")
    soc.add_argument("--where", help="exact SoQL $where filter expression")
    soc.add_argument("--select", help="SoQL $select projection")
    soc.add_argument("--order", help="SoQL $order clause")
    _limit(soc)

    arc = sub.add_parser("arcgis", help="Generic Esri ArcGIS REST FeatureServer / MapServer driver (county parcels, GIS)")
    arc.add_argument("query", nargs="?", default="", help="value to match against --field (or SQL WHERE clause)")
    arc.add_argument("--layer-url", dest="layer_url", required=True,
                     help="ArcGIS REST layer URL (.../FeatureServer/0 or .../MapServer/0)")
    arc.add_argument("--field", help="attribute column name for substring search")
    arc.add_argument("--where", help="explicit ArcGIS SQL WHERE clause")
    arc.add_argument("--out-fields", dest="out_fields", default="*", help="comma-separated output fields (default: *)")
    _limit(arc)

    kv = sub.add_parser("kev", help="CISA Known Exploited Vulnerabilities (KEV) & NIST NVD CVE 2.0")
    kv.add_argument("query", nargs="?", default="", help="CVE ID, vendor, product, or keyword")
    kv.add_argument("--vendor", help="filter CISA KEV by vendorProject")
    kv.add_argument("--product", help="filter CISA KEV by product")
    kv.add_argument("--cve", dest="cve_id", help="exact CVE ID (e.g. CVE-2024-21887)")
    kv.add_argument("--ransomware-only", action="store_true", help="only KEVs with known ransomware campaign use")
    kv.add_argument("--nvd", action="store_true", help="query NIST NVD CVE 2.0 API instead of CISA KEV catalog")
    _limit(kv)

    ct = sub.add_parser("crtsh", help="crt.sh Certificate Transparency log search (subdomains & shared SANs)")
    ct.add_argument("domain", help="domain or wildcard (e.g. example.gov or %%.example.gov)")
    ct.add_argument("--active-only", action="store_true", help="exclude expired certificates")
    _limit(ct)

    ep = sub.add_parser("epa", help="EPA ECHO regulated facilities, non-compliance quarters & penalties")
    ep.add_argument("query", nargs="?", default="", help="facility name")
    ep.add_argument("--state", help="two-letter state filter")
    ep.add_argument("--zip", dest="zip_code", help="5-digit ZIP code filter")
    ep.add_argument("--violators-only", action="store_true", help="only facilities currently in non-compliance")
    _limit(ep)

    of = sub.add_parser("openfda", help="OpenFDA device recalls, 510(k) clearances, enforcement & FAERS")
    of.add_argument("query", nargs="?", default="", help="device, drug, or Lucene query")
    of.add_argument("--endpoint", choices=["recall", "510k", "enforcement", "drug_event"], default="recall",
                    help="OpenFDA endpoint (default: recall)")
    of.add_argument("--firm", help="exact recalling_firm / applicant filter")
    _limit(of)

    cf = sub.add_parser("cfpb", help="CFPB Consumer Complaint Database search and narratives")
    cf.add_argument("query", nargs="?", default="", help="full-text search across complaint narratives")
    cf.add_argument("--company", help="exact company name filter")
    cf.add_argument("--product", help="product category filter")
    cf.add_argument("--state", help="two-letter state filter")
    _limit(cf)

    ic = sub.add_parser("icij", help="ICIJ Offshore Leaks bulk SQLite database (#5)")
    ic.add_argument("query", help="entity, officer, or intermediary name")
    ic.add_argument("--type", dest="node_type", choices=["all", "entity", "officer", "intermediary"],
                    default="all", help="node type filter (default: all)")
    ic.add_argument("--jurisdiction", help="jurisdiction substring filter (e.g. British Virgin Islands)")
    ic.add_argument("--dataset", help="source leak dataset filter (e.g. Panama Papers)")
    ic.add_argument("--refresh", action="store_true", help="force re-download of bulk ICIJ archive")
    ic.add_argument("--cache-dir", help="local bulk index cache directory")
    _limit(ic)

    br = sub.add_parser("brokers", help="State statutory data-broker registries (CA, VT, TX, OR) (#20)")
    br.add_argument("query", help="company name, DBA, or domain")
    br.add_argument("--state", default="all", help="state code (CA, VT, TX, OR, or all)")
    br.add_argument("--exact", action="store_true", help="exact normalized name/DBA match only")
    br.add_argument("--refresh", action="store_true", help="force re-download of state broker CSV feeds")
    br.add_argument("--cache-dir", help="local bulk index cache directory")
    _limit(br)

    gd = sub.add_parser("gdelt", help="GDELT DOC 2.0 global news article discovery API (#6)")
    gd.add_argument("query", help="search phrase or keywords")
    gd.add_argument("--domain", help="restrict to domain (domain:...)")
    gd.add_argument("--country", help="source country filter (sourcecountry:...)")
    gd.add_argument("--language", help="source language filter (sourcelang:...)")
    gd.add_argument("--timespan", help="window such as 7d, 24h, 3m")
    _limit(gd)

    # Dynamic top-level subcommands for declarative manifest / entry-point plugins
    from .core.registry import all_specs as _reg_all_specs
    _BUILTIN_CMDS = {
        "news", "web", "oscn", "courtlistener", "propublica", "usaspending",
        "crossref", "fedreg", "fec", "lda", "samgov", "nonprofit", "sec",
        "sanctions", "fara", "wayback", "openalex", "socrata", "arcgis",
        "kev", "crtsh", "epa", "openfda", "cfpb", "icij", "brokers", "gdelt",
        "investigate", "mcp", "plugin", "docs", "browser", "gate", "extract",
        "record", "limits", "hosts", "sources",
    }
    _dynamic_plugins: dict[str, object] = {}
    for _sp in _reg_all_specs():
        if _sp.run is not None and _sp.name not in _BUILTIN_CMDS:
            _dp = sub.add_parser(_sp.name, help=f"[plugin] {_sp.summary}")
            _dp.add_argument("query", nargs="?", default="", help=f"query for {_sp.name}")
            _dp.add_argument("--where", help="optional SoQL / ArcGIS SQL WHERE filter")
            _limit(_dp)
            _dynamic_plugins[_sp.name] = _sp

    inv = sub.add_parser("investigate", help="cross-source sweep with automatic CIK/EIN/UEI/domain pivots")
    inv.add_argument("query", help="target entity, contractor, or organization name")
    inv.add_argument("--sources", help="comma-separated sources to query (default: core investigative suite)")
    inv.add_argument("--domain", help="optional domain to include crtsh, wayback, and TLS subdomain pivots")
    _limit(inv, default=5)

    mp = sub.add_parser("mcp", help="Phase 2 MCP JSON-RPC 2.0 server over stdio")
    mp.add_argument("--list-tools", action="store_true", help="print MCP tool schemas as JSON and exit")
    mp.add_argument("--call", metavar="TOOL", help="call one MCP tool synchronously and print JSON result")
    mp.add_argument("--args", default="{}", help="JSON arguments object for --call (default: '{}')")

    pl = sub.add_parser("plugin", help="plugin ecosystem: list, check, init, or run plugins")
    pls = pl.add_subparsers(dest="plugin_cmd", required=True)
    pls.add_parser("list", help="list all built-in, entry-point, and declarative manifest plugins")
    plc = pls.add_parser("check", help="run 6-point epistemic-manifest and absence-safety conformance suite")
    plc.add_argument("source", nargs="?", default=None, help="optional source/plugin name to check (default: all)")
    plc.add_argument("--live", action="store_true", help="also execute a live network canary against the upstream API")
    plr = pls.add_parser("run", help="run a declarative manifest or registered plugin by name")
    plr.add_argument("name", help="registered plugin name")
    plr.add_argument("query", nargs="?", default="", help="search query")
    plr.add_argument("--where", help="optional SoQL / SQL WHERE filter for socrata/arcgis manifest plugins")
    _limit(plr)
    pli = pls.add_parser("init", help="scaffold a validated declarative JSON plugin manifest")
    pli.add_argument("name", help="plugin command name (e.g. texas_contracts)")
    pli.add_argument("--driver", required=True, choices=["socrata", "arcgis", "rest"])
    pli.add_argument("--domain", help="Socrata portal host (for --driver socrata)")
    pli.add_argument("--dataset", help="Socrata 4x4 dataset ID (for --driver socrata)")
    pli.add_argument("--layer-url", dest="layer_url", help="ArcGIS FeatureServer/MapServer layer URL (for --driver arcgis)")
    pli.add_argument("--endpoint", help="REST JSON endpoint URL (for --driver rest)")
    pli.add_argument("--rows-path", dest="rows_path", help="dotted key path to row array (for --driver rest, default: results)")
    pli.add_argument("--total-path", dest="total_path", help="dotted key path to total count (for --driver rest)")
    pli.add_argument("--search-field", dest="search_field", help="default column name for text queries")
    pli.add_argument("--summary", help="one-line description for `evidence-search sources`")
    pli.add_argument("--dir", dest="target_dir", help="override plugin directory (default ~/.evidence-search/plugins)")

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

    g = sub.add_parser("gate", help="humanomation: list, resume, and cancel human gates")
    gs = g.add_subparsers(dest="gate_cmd", required=True)
    gs.add_parser("list")
    gu = gs.add_parser("ui", help="open the gate worklist in a browser (easiest path)")
    gu.add_argument("--port", type=int, default=8787)
    gu.add_argument("--no-open", action="store_true", help="print the URL, do not launch a browser")
    gr = gs.add_parser("resume"); gr.add_argument("token"); gr.add_argument("--file", action="append")
    gc = gs.add_parser("cancel", help="dismiss one gate by token, or bulk-cancel by --older-than / --source")
    gc.add_argument("token", nargs="?", default=None, help="gate token to cancel")
    gc.add_argument("--older-than", dest="older_than",
                    help="bulk-cancel gates older than duration (e.g. 30d, 24h, 60m)")
    gc.add_argument("--source", help="bulk-cancel gates from this source")
    gc.add_argument("--reason", default="", help="reason recorded in the ledger")

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

    lim = sub.add_parser(
        "limits", help="show per-source rate policy, usage, 429 blocks and backoff",
        description="With no arguments: every source's windows and usage, any shared "
                    "429 block, and the failure backoff. `limits reserve SOURCE` "
                    "claims one call in the shared ledger for an EXTERNAL tool "
                    "(exit 0 reserved, 13 refused); `limits block SOURCE RETRY_AFTER` "
                    "records a 429 that tool received.")
    lim.add_argument("action", nargs="?", choices=["reserve", "block"])
    lim.add_argument("source", nargs="?", help="a source name, or a URL (charged to its bucket)")
    lim.add_argument("retry_after", nargs="?",
                     help="block only: the Retry-After value (seconds or HTTP-date)")
    sub.add_parser("hosts", help="show the browser-tier public-records allow-list")
    sr = sub.add_parser("sources", help="show the source manifest catalog and epistemic boundaries")
    sr.add_argument("source", nargs="?", default=None, help="optional source name to inspect")

    _argv = list(sys.argv[1:] if argv is None else argv)
    for _g in ("--json", "--no-cache", "--wait"):
        if _g in _argv and _argv.index(_g) > 0:
            _argv.remove(_g)
            _argv.insert(0, _g)
    for _i, _tok in enumerate(_argv):
        if _i > 0 and _tok == "--wait-max" and _i + 1 < len(_argv):
            _argv[0:0] = [_argv.pop(_i), _argv.pop(_i)]
            break
        if _i > 0 and _tok.startswith("--wait-max="):
            _argv.insert(0, _argv.pop(_i))
            break
    a = p.parse_args(_argv)
    if a.wait_max < 0:
        p.error("--wait-max must be 0 or more seconds")
    store = Store(); limiter = Limiter(store); use_cache = not a.no_cache
    if getattr(a, "wait", False):
        limiter.wait_for_spacing = True
    limiter.wait_max_s = a.wait_max

    src_rc = _dispatch_source_cmd(a, store, limiter, use_cache, _dynamic_plugins)
    if src_rc is not None:
        return src_rc

    if a.cmd == "investigate":
        from .core import investigate as _inv
        sources = [s.strip() for s in a.sources.split(",") if s.strip()] if a.sources else None
        report = _inv.run_investigation(
            a.query, sources=sources, domain=a.domain,
            limit=a.limit or 5, store=store, limiter=limiter, use_cache=use_cache,
        )
        if a.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            print(_inv.format_investigation_report(report))
        summ = report["summary"]
        if summ["hit_sources"]:
            return EXIT_HIT
        if not summ["is_complete"]:
            return EXIT_ACCESS_BLOCKER
        if summ["verified_absence_sources"]:
            return EXIT_VERIFIED_ABSENCE
        return 0

    if a.cmd == "mcp":
        from . import mcp_server
        if a.list_tools:
            print(json.dumps(mcp_server.list_mcp_tools(), indent=2))
            return 0
        if a.call:
            try:
                call_args = json.loads(a.args or "{}")
            except json.JSONDecodeError as e:
                print(f"mcp --args: invalid JSON: {e}", file=sys.stderr)
                return EXIT_USAGE
            res = mcp_server.call_mcp_tool(a.call, call_args, store=store, limiter=limiter)
            print(json.dumps(res, indent=2, default=str))
            return EXIT_USAGE if res.get("isError") else 0
        return mcp_server.serve_stdio(store=store, limiter=limiter)

    if a.cmd == "plugin":
        from .core.registry import all_specs, get_spec, scaffold_manifest
        from .core.conformance import check_all_sources, check_source
        if a.plugin_cmd == "list":
            specs = all_specs()
            if a.json:
                print(json.dumps([s.to_dict() for s in specs], indent=2))
                return 0
            print(f"== {len(specs)} registered source plugins ==\n")
            for s in specs:
                print(f"  {s.name:<20} [{s.origin:<16} | {s.driver:<7}]  {s.summary}")
            return 0
        if a.plugin_cmd == "check":
            reports = [check_source(a.source, live=a.live)] if a.source else check_all_sources(live=a.live)
            all_ok = all(r["passed"] for r in reports)
            if a.json:
                print(json.dumps({"passed": all_ok, "reports": reports}, indent=2))
                return 0 if all_ok else EXIT_INTERNAL_ERROR
            for r in reports:
                status = "PASS" if r["passed"] else "FAIL"
                print(f"[{status}] {r['name']:<20} (origin={r.get('origin','?')}, driver={r.get('driver','?')})")
                for ck, ok in (r.get("checks") or {}).items():
                    print(f"       {'ok' if ok else '!!'} {ck}")
                if r.get("live"):
                    lv = r["live"]
                    print(f"       live: {lv.get('live_outcome')} ({lv.get('live_elapsed_ms')}ms)")
                for err in (r.get("errors") or []):
                    print(f"       ERROR: {err}")
            return 0 if all_ok else EXIT_INTERNAL_ERROR
        if a.plugin_cmd == "run":
            spec = get_spec(a.name)
            if spec is None or spec.run is None:
                print(f"plugin run: {a.name!r} is not a runnable manifest/callable plugin", file=sys.stderr)
                return EXIT_USAGE
            return _emit(spec.run(a.query, where=a.where, limit=a.limit or 20,
                                  store=store, limiter=limiter, use_cache=use_cache),
                         a.json, a.limit)
        if a.plugin_cmd == "init":
            try:
                out_path = scaffold_manifest(
                    a.name, driver=a.driver, domain=a.domain, dataset_id=a.dataset,
                    layer_url=a.layer_url, endpoint=a.endpoint, rows_path=a.rows_path,
                    total_path=a.total_path, search_field=a.search_field,
                    summary=a.summary, target_dir=a.target_dir,
                )
            except ValueError as e:
                print(f"plugin init: {e}", file=sys.stderr)
                return EXIT_USAGE
            rep = check_source(a.name)
            if a.json:
                print(json.dumps({"manifest_path": str(out_path), "conformance": rep}, indent=2))
            else:
                print(f"created: {out_path}")
                print(f"conformance [{('PASS' if rep['passed'] else 'FAIL')}]: {', '.join(k for k, v in rep['checks'].items() if v)}")
            return 0 if rep["passed"] else EXIT_INTERNAL_ERROR

    if a.cmd == "browser":
        from .core import browser
        from .core.archive import archive as _archive
        if browser.host_allowed(a.url)[0]:
            _, _refused = _reserve_fetch(limiter, a.url, via_browser=True)
            if _refused:
                return _emit(_refused, a.json)
        backend = browser.current_backend(headless=not a.headed)
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
                                      meta={"archived": rec, "bytes": len(html),
                                            "render_backend": backend})]), a.json)

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
                req = f"  (task: {p['requested_by']})" if p.get("requested_by") else ""
                print(f"  {j['token']}  [{p.get('gate_type','?')}]  {j['source']}{req}")
                print(f"    {p.get('url','')}")
                if p.get("capture"):
                    print(f"    capture: {', '.join(p['capture'])}")
            return EXIT_AWAITING_HUMAN
        if a.gate_cmd == "cancel":
            older_s = None
            if a.older_than:
                try:
                    older_s = gates.parse_duration(a.older_than)
                except ValueError as e:
                    print(f"gate cancel: {e}", file=sys.stderr)
                    return EXIT_USAGE
            cancelled, err = gates.cancel(store, a.token, older_than_s=older_s,
                                          source=a.source, reason=a.reason)
            if err:
                code = EXIT_USAGE if "specify a gate token" in err else EXIT_INTERNAL_ERROR
                print(f"ERROR: {err}", file=sys.stderr)
                return code
            if a.json:
                print(json.dumps({"cancelled": cancelled, "count": len(cancelled)}, indent=2))
            else:
                if not cancelled:
                    print("No matching open gates to cancel.")
                    return EXIT_NO_LOCAL_DATA
                print(f"Cancelled {len(cancelled)} gate(s): {', '.join(cancelled)}")
            return 0
        res, err = gates.resume(store, a.token, a.file or [])
        if err:
            print(f"ERROR: {err}"); return EXIT_INTERNAL_ERROR
        return _emit(res, a.json)

    if a.cmd == "extract":
        from pathlib import Path as _P
        from .core import extract as _x
        from .core.http import fetch as _fetch
        from .core.results import Blocker as _Blocker

        _pdf_provenance = None
        _render_backend = None
        is_url = a.target.startswith(("http://", "https://"))
        if is_url:
            from .core import browser as _b
            cdp_active = bool((os.environ.get(_b.CDP_ENV) or "").strip())
            if a.browser:
                if _b.host_allowed(a.target)[0] or cdp_active:
                    _, _refused = _reserve_fetch(limiter, a.target, via_browser=True)
                    if _refused:
                        return _emit(_refused, a.json)
                _render_backend = _b.current_backend(headless=True)
                raw, out = _b.fetch(a.target, source="extract", query=a.target,
                                    store=store, allow_ungated_js=cdp_active)
            else:
                _bucket, _refused = _reserve_fetch(limiter, a.target)
                if _refused:
                    return _emit(_refused, a.json)
                raw, out = _fetch(a.target, source=_bucket, query=a.target)
                limiter.note_outcome(_bucket, out, count_failure=False)
                # Issue #26: when an optional CDP render backend is configured
                # (EVIDENCE_BROWSER_CDP) and the plain fetch hit an ungated
                # JS-only SPA shell, returned <200 visible chars with no blocker,
                # or returned a script-driven page where --grep found 0 matches
                # in the unhydrated SSR shell, render through the CDP backend.
                _needs_cdp = False
                if cdp_active:
                    if out is not None and isinstance(out, AccessBlocker) and out.mechanism is _Blocker.JS_ONLY:
                        _needs_cdp = True
                    elif out is None and raw and not raw.lstrip()[:5].startswith("%PDF"):
                        if len(_x.page_text(raw)) < EXTRACT_MIN_VISIBLE_CHARS:
                            _needs_cdp = True
                        elif a.grep and "<script" in raw.lower():
                            _pre = [m for m in _x.grep(raw, a.grep, is_html=True)
                                    if m["pattern"] != "__truncated__"]
                            if not _pre:
                                _needs_cdp = True
                if _needs_cdp:
                    _, _refused = _reserve_fetch(limiter, a.target, via_browser=True)
                    if not _refused:
                        b_raw, b_out = _b.fetch(a.target, source="extract", query=a.target,
                                                store=store, allow_ungated_js=True)
                        if b_raw and not b_out:
                            raw, out = b_raw, None
                            _render_backend = _b.current_backend(headless=True)
            if out:
                return _emit(out, a.json)

            if raw and raw.lstrip()[:5].startswith("%PDF"):
                import tempfile as _tf
                _bucket, _refused = _reserve_fetch(limiter, a.target)
                if _refused:
                    return _emit(_refused, a.json)
                _bin, _berr = _fetch(a.target, source=_bucket, query=a.target, binary=True)
                limiter.note_outcome(_bucket, _berr, count_failure=False)
                if _berr:
                    return _emit(_berr, a.json)
                with _tf.NamedTemporaryFile(suffix=".pdf", delete=False) as _tmp:
                    _tmp.write(_bin)
                    _tmp_path = _P(_tmp.name)
                try:
                    raw, _prov, _pdf_blocked = _read_pdf(
                        _tmp_path, a.target, not a.no_ocr, a.ocr_pages, "remote PDF")
                finally:
                    _tmp_path.unlink(missing_ok=True)
                if _pdf_blocked:
                    return _emit(_pdf_blocked, a.json)
                _pdf_provenance = _prov
        else:
            _path = _P(a.target).expanduser()
            _head = b""
            try:
                with open(_path, "rb") as _fh:
                    _head = _fh.read(5)
            except OSError:
                pass
            if _head.startswith(b"%PDF"):
                raw, _prov, _pdf_blocked = _read_pdf(
                    _path, a.target, not a.no_ocr, a.ocr_pages)
                if _pdf_blocked:
                    return _emit(_pdf_blocked, a.json)
                _pdf_provenance = _prov
            else:
                raw = _path.read_text(errors="replace")
        is_html = (is_url or a.target.endswith((".html", ".htm"))) and not _pdf_provenance

        payload = {}
        if a.grep:
            payload["matches"] = _x.grep(raw, a.grep, is_html=is_html)
            _found = [m for m in payload["matches"] if m["pattern"] != "__truncated__"]
            _visible = len(_x.page_text(raw)) if is_html else len(raw.strip())
            if not _found and is_html and _visible < EXTRACT_MIN_VISIBLE_CHARS:
                from .core.results import AccessBlocker as _AB, Blocker as _B, Coverage as _Cv
                return _emit(_AB(
                    query=a.target,
                    coverage=_Cv(queried=["extract"], errored={"extract": "no-visible-text"}),
                    mechanism=_B.SERVER_ERROR, url=a.target,
                    detail=(f"Only {_visible} characters of visible text were served "
                            f"({len(raw):,} characters fetched). That is too little to "
                            "be the document: a stub, a script-rendered page or an "
                            "unrecognised wall. NO MATCHES here is NOT an absence. "
                            "Run with --text to see what was served."
                            + ("" if (not is_url or a.browser) else
                               " Then retry with --browser."))), a.json)
        if a.ids:
            payload["identifiers"] = _x.identifiers(raw, is_html=is_html)
        if a.tables:
            payload["tables"] = _x.tables(raw) if is_html else []
        if a.text or not payload:
            payload["text"] = _x.page_text(raw, a.max_chars) if is_html else raw[:a.max_chars or None]

        if _pdf_provenance:
            payload["provenance"] = _pdf_provenance
        if _render_backend:
            payload["render_backend"] = _render_backend
        stats = _x.savings(raw, json.dumps(payload, default=str))
        if a.json:
            print(json.dumps({"target": a.target, **payload, "savings": stats}, indent=2, default=str))
        else:
            print(f"== Extract == {a.target}")
            if _render_backend:
                print(f"rendered: {_render_backend}")
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
                pats = ", ".join(repr(x) for x in (a.grep or []))
                print(f"  NO MATCHES for {pats} in {max(1, _visible // _x.CHARS_PER_TOKEN):,} tokens "
                      "of visible text.\n  This is an absence IN THIS DOCUMENT ONLY -- the "
                      "document was read, the terms are not in it.")
            for m in payload.get("matches", []):
                if m["pattern"] == "__truncated__":
                    print(f"  !! {m['context']}")
                    continue
                print(f"  [{m['pattern']}] ...{m['context']}...")
            if payload.get("identifiers") and (_pdf_provenance or {}).get("ocr"):
                print("  !! identifiers below came from OCR -- verify each against "
                      "the page image before citing")
            for k, v in (payload.get("identifiers") or {}).items():
                print(f"  {k:14} {', '.join(v[:10])}")
            for i, t in enumerate(payload.get("tables", [])[:5], 1):
                print(f"  table {i}: {len(t)} rows | {' | '.join(t[0][:5])}")
            if "text" in payload:
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
        from .core.limits import HOST_PREFIX, POLICIES, bucket_for_url, effective_policy
        if a.action:
            name = a.source or ""
            if name.startswith(("http://", "https://")):
                name = bucket_for_url(name)
            if name not in POLICIES and not name.startswith(HOST_PREFIX):
                print(f"limits {a.action}: unknown source {a.source!r} "
                      f"(known: {', '.join(sorted(POLICIES))}, or a URL)", file=sys.stderr)
                return EXIT_USAGE
            if a.action == "block":
                from .core.http import retry_after_seconds
                import time as _t
                secs = retry_after_seconds(a.retry_after)
                store.block_until(name, _t.time() + secs, "HTTP 429 (reported by an external tool)")
                print(json.dumps({"source": name, "blocked_for_s": secs}) if a.json
                      else f"{name}: blocked for every process for {secs}s")
                return 0
            ok, retry, why = limiter.reserve(name)
            if ok:
                print(json.dumps({"source": name, "reserved": True}) if a.json
                      else f"{name}: reserved one call")
                return 0
            import math
            from .core.results import Coverage as _Cv
            return _emit(RateLimited(
                query=f"limits reserve {name}",
                coverage=_Cv(queried=[name], rate_limited=[name]), source=name,
                retry_after_s=math.ceil(retry) if retry else None, detail=why), a.json)

        import time as _t
        rows = []
        for name in sorted(POLICIES) + store.active_sources(HOST_PREFIX):
            pol, origin = effective_policy(name)
            st = limiter.status(name)
            used = [f"{u['used']}/{u['max']} per {int(u['window_s'])}s" for u in st["usage"]]
            rows.append({"source": name, "windows": used, "cacheable": pol.cacheable,
                         "index_origin": pol.index_origin, "note": pol.note,
                         **{k: v for k, v in st.items() if k != "source"}})
        if a.json:
            print(json.dumps(rows, indent=2))
        else:
            for r in rows:
                print(f"\n{r['source']}")
                print(f"  usage:     {', '.join(r['windows'])}")
                if r["session"]:
                    ss = r["session"]
                    print(f"  session:   {ss['used']}/{ss['max']} per {int(ss['window_s'])}s")
                if r["windows_from"] != "built-in":
                    print(f"  OVERRIDE:  windows set by {r['windows_from']}; "
                          "the note below describes the built-in policy")
                if r["blocked_until"]:
                    when = _t.strftime("%Y-%m-%d %H:%M:%S", _t.localtime(r["blocked_until"]))
                    print(f"  blocked:   until {when} ({int(r['blocked_for_s']) + 1}s left) "
                          f"-- {r['blocked_reason']}; shared by every process")
                else:
                    print("  blocked:   no")
                bo = r["backoff"]
                if bo["remaining_s"] > 0:
                    print(f"  backoff:   {bo['consecutive_failures']} consecutive failures "
                          f"({bo['last_reason']}), {int(bo['remaining_s']) + 1}s left")
                elif bo["consecutive_failures"]:
                    print(f"  backoff:   clear ({bo['consecutive_failures']} consecutive "
                          f"failure(s) on record: {bo['last_reason']})")
                else:
                    print("  backoff:   clear")
                print("  next call: " + ("allowed" if r["next_call"] == "allowed"
                                         else f"REFUSED -- {r['next_call']}"))
                print(f"  cacheable: {r['cacheable']}   index: {r['index_origin']}")
                if r["note"]:
                    print(f"  note:      {r['note']}")
        return 0

    if a.cmd == "hosts":
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

    if a.cmd == "sources":
        from .core.registry import all_specs, get_spec
        if a.source:
            spec = get_spec(a.source)
            if spec is None:
                known = ", ".join(s.name for s in all_specs())
                print(f"sources: unknown source {a.source!r} (known: {known})", file=sys.stderr)
                return EXIT_USAGE
            specs = [spec]
        else:
            specs = all_specs()
        if a.json:
            payload = specs[0].to_dict() if a.source else [s.to_dict() for s in specs]
            print(json.dumps(payload, indent=2))
            return 0
        print("== Registered sources & epistemic boundaries ==\n")
        for s in specs:
            exact = ("yes" if s.exact_match_supported is True
                     else "no (FUZZY)" if s.exact_match_supported is False
                     else "query-dependent")
            cnt = "yes (countable)" if s.counts else "no (not countable)"
            print(f"{s.name:<15} [{s.policy_name}] — {s.summary}")
            print(f"  corpus:       {s.corpus}")
            print(f"  counts:       {cnt}   |   exact match: {exact}")
            if s.requires_key:
                print(f"  auth/key:     {s.requires_key}")
            print(f"  NOT searched: {'; '.join(s.not_searched)}")
            for c_ in s.caveats:
                print(f"  caveat:       !! {c_}")
            print()
        return 0

    return EXIT_USAGE


def cli() -> int:
    """Console entry point."""
    try:
        return main()
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"evidence-search: internal error: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        print("This is a TOOL FAILURE, not a finding about the world.",
              file=sys.stderr)
        return EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    sys.exit(cli())
