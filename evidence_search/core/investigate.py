"""Cross-source identifier pivoting and unified dispatch (`evidence-search investigate`).

Runs a target query across a curated set of primary sources (plus any requested
manifest plugins), extracts structured identifiers discovered in `Hit.results`
(`CIK`, `EIN`, `UEI`, `award_id`/`piid`, `reg_number`, `docket_number`, and
domains), executes secondary deep-dive pivot queries against authoritative
record endpoints (`nonprofit.by_ein`, `sec.submissions`, `crtsh.search`), and
returns a provenance-preserving investigation report separating hits, bounded
verified absences, and incomplete/blocked sources.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Sequence
from urllib.parse import urlsplit

from . import extract as _extract
from .limits import Limiter
from .registry import get_spec
from .results import (
    AccessBlocker,
    AwaitingHuman,
    Blocker,
    Coverage,
    Hit,
    Outcome,
    RateLimited,
    VerifiedAbsence,
)
from .store import Store

DEFAULT_SOURCES: tuple[str, ...] = (
    "usaspending",
    "samgov",
    "sec",
    "nonprofit",
    "lda",
    "fara",
    "sanctions",
    "icij",
    "epa",
    "cfpb",
)

_EIN_RE = re.compile(_extract.IDENTIFIERS["ein"])
_CIK_URL_RE = re.compile(r"(?:CIK=|/edgar/browse/\?CIK=)(\d{4,10})", re.I)
_UEI_RE = re.compile(_extract.IDENTIFIERS["uei"])
_AWARD_RE = re.compile(_extract.IDENTIFIERS["federal_award"])
_DOCKET_RE = re.compile(_extract.IDENTIFIERS["docket"])
_DOMAIN_SUFFIX_GOV = (".gov", ".mil", "sec.gov", "courtlistener.com", "usaspending.gov",
                      "sam.gov", "propublica.org", "efile.fara.gov", "icij.org")


def run_single_source(
    source_name: str,
    query: str,
    *,
    limit: int = 5,
    store: Store | None = None,
    limiter: Limiter | None = None,
    use_cache: bool = True,
    extra: dict[str, Any] | None = None,
) -> Outcome:
    """Invoke any registered built-in source or plugin by name and return an Outcome."""
    store = store or Store()
    limiter = limiter or Limiter(store)
    ex = dict(extra or {})
    cap = max(1, int(limit))

    spec = get_spec(source_name)
    if spec is not None and spec.run is not None:
        return spec.run(
            query,
            where=ex.get("where"),
            limit=cap,
            store=store,
            limiter=limiter,
            use_cache=use_cache,
        )

    name = "web" if source_name == "searxng" else source_name
    from ..engines import news_rss, web
    from ..sources import (
        brokers,
        cfpb,
        courtlistener,
        crossref,
        crtsh,
        docs,
        epa,
        fara,
        fec,
        federal_register,
        gdelt,
        icij,
        kev,
        lda,
        nonprofit,
        openalex,
        openfda,
        oscn,
        propublica_disclosures,
        samgov,
        sanctions,
        sec,
        usaspending,
        wayback,
    )

    def _courtlistener() -> Outcome:
        if ex.get("docket"):
            return courtlistener.docket(
                str(ex["docket"]),
                since=ex.get("since"),
                grep_pattern=ex.get("grep"),
                store=store,
                limiter=limiter,
                use_cache=use_cache,
            )
        return courtlistener.search(
            query, ex.get("type", "r"), ex.get("court"), store, limiter, use_cache
        )

    def _usaspending() -> Outcome:
        if ex.get("detail"):
            return usaspending.detail(str(ex["detail"]), store, limiter)
        if ex.get("idv"):
            return usaspending.idv_orders(
                str(ex["idv"]), limit=cap, page=int(ex.get("page", 1)),
                store=store, limiter=limiter, use_cache=use_cache,
            )
        if ex.get("transactions"):
            return usaspending.transactions(
                str(ex["transactions"]), limit=cap, page=int(ex.get("page", 1)),
                store=store, limiter=limiter, use_cache=use_cache,
            )
        by_rec = bool(ex.get("by_recipient", True))
        if ex.get("count"):
            return usaspending.counts(
                query, by_recipient=by_rec, agency=ex.get("agency"),
                store=store, limiter=limiter, use_cache=use_cache,
            )
        if ex.get("sum"):
            return usaspending.dollar_sum(
                query, by_recipient=by_rec, agency=ex.get("agency"),
                store=store, limiter=limiter,
            )
        return usaspending.search(
            query, by_recipient=by_rec, agency=ex.get("agency"),
            limit=cap, store=store, limiter=limiter, use_cache=use_cache,
        )

    def _samgov() -> Outcome:
        if ex.get("attachments"):
            return samgov.attachments(
                str(ex["attachments"]), download=bool(ex.get("download", False)),
                store=store, limiter=limiter, use_cache=use_cache,
            )
        opp_id = ex.get("detail") or ex.get("opp_id")
        if opp_id:
            return samgov.detail(str(opp_id), store=store, limiter=limiter, use_cache=use_cache)
        return samgov.search(
            query, active_only=bool(ex.get("active_only", False)),
            limit=cap, store=store, limiter=limiter, use_cache=use_cache,
        )

    def _fec() -> Outcome:
        c_from, c_to = fec.parse_cycle_range(str(ex.get("cycles") or "2024"))
        if ex.get("committee"):
            return fec.lookup_committee(
                str(ex["committee"]), c_from, c_to, store=store, limiter=limiter, use_cache=use_cache
            )
        fc = str(ex.get("from_committee") or query)
        tc = str(ex.get("to_committee") or "")
        return fec.between(fc, tc, c_from, c_to, store=store, limiter=limiter, use_cache=use_cache)

    def _oscn() -> Outcome:
        county = str(ex.get("county") or "oklahoma")
        if ex.get("case"):
            return oscn.case(county, str(ex["case"]), store, limiter)
        return oscn.search(
            county, str(ex.get("lname") or query), str(ex.get("fname") or ""),
            ex.get("year"), store, limiter, use_cache,
        )

    dispatch: dict[str, Callable[[], Outcome]] = {
        "news": lambda: news_rss.search(query, store, limiter, use_cache),
        "web": lambda: web.search(
            query,
            ex.get("categories", "general"),
            int(ex.get("page", 1)),
            ex.get("base"),
            store,
            limiter,
            use_cache,
            provider=ex.get("provider", "auto"),
            limit=cap,
        ),
        "oscn": _oscn,
        "courtlistener": _courtlistener,
        "propublica": lambda: propublica_disclosures.search(query, store, limiter, use_cache),
        "usaspending": _usaspending,
        "crossref": lambda: crossref.search(query, cap, store, limiter, use_cache),
        "fedreg": lambda: federal_register.search(
            query, per_page=cap, store=store, limiter=limiter, use_cache=use_cache
        ),
        "docs": lambda: docs.search(
            query, ex.get("site", "claude-code"), int(ex.get("fetch_top", 0)),
            store, limiter, use_cache,
        ),
        "lda": lambda: lda.search(client_name=query, store=store, limiter=limiter, use_cache=use_cache),
        "fec": _fec,
        "samgov": _samgov,
        "nonprofit": lambda: (
            nonprofit.by_ein(str(ex["ein"]), year=ex.get("year"), store=store, limiter=limiter, use_cache=use_cache)
            if ex.get("ein")
            else nonprofit.search(query, state=ex.get("state"), store=store, limiter=limiter, use_cache=use_cache)
        ),
        "sec": lambda: (
            sec.submissions(str(ex.get("company") or ex.get("cik")), forms=ex.get("forms"),
                            limit=cap, store=store, limiter=limiter, use_cache=use_cache)
            if (ex.get("company") or ex.get("cik"))
            else sec.fulltext(
                f'"{query}"' if " " in query and not query.startswith('"') else query,
                forms=ex.get("forms"), limit=cap, store=store, limiter=limiter, use_cache=use_cache,
            )
        ),
        "sanctions": lambda: sanctions.search(
            query,
            exact_only=bool(ex.get("exact", False)),
            store=store,
            limiter=limiter,
            cache_dir=ex.get("cache_dir"),
            use_cache=use_cache,
        ),
        "fara": lambda: fara.search(
            query, refresh=bool(ex.get("refresh", False)), limit=cap, store=store,
            limiter=limiter, cache_dir=ex.get("cache_dir"), use_cache=use_cache,
        ),
        "wayback": lambda: wayback.search(query, limit=cap, store=store, limiter=limiter, use_cache=use_cache),
        "openalex": lambda: openalex.search(query, limit=cap, store=store, limiter=limiter, use_cache=use_cache),
        "kev": lambda: kev.search(query, limit=cap, store=store, limiter=limiter, use_cache=use_cache),
        "crtsh": lambda: crtsh.search(query, limit=cap, store=store, limiter=limiter, use_cache=use_cache),
        "epa": lambda: epa.search(query, state=ex.get("state"), limit=cap, store=store, limiter=limiter, use_cache=use_cache),
        "openfda": lambda: openfda.search(
            query, endpoint=ex.get("endpoint", "recall"), limit=cap, store=store, limiter=limiter, use_cache=use_cache
        ),
        "cfpb": lambda: cfpb.search(query, limit=cap, store=store, limiter=limiter, use_cache=use_cache),
        "icij": lambda: icij.search(
            query, refresh=bool(ex.get("refresh", False)), limit=cap, store=store, limiter=limiter,
            cache_dir=ex.get("cache_dir"), use_cache=use_cache,
        ),
        "brokers": lambda: brokers.search(
            query, state=ex.get("state", "all"), refresh=bool(ex.get("refresh", False)), limit=cap, store=store,
            limiter=limiter, cache_dir=ex.get("cache_dir"), use_cache=use_cache,
        ),
        "gdelt": lambda: gdelt.search(query, limit=cap, store=store, limiter=limiter, use_cache=use_cache),
    }
    fn = dispatch.get(name)
    if fn is None:
        return AccessBlocker(
            query=query,
            coverage=Coverage(queried=[source_name], errored={source_name: "unknown-source"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url="",
            detail=f"Source {source_name!r} is not registered or requires explicit target flags.",
        )
    return fn()


def _add_unique(bucket: list[str], value: Any) -> None:
    if value is None:
        return
    s = str(value).strip()
    if s and s not in bucket:
        bucket.append(s)


def _normalize_domain(raw: str) -> str:
    s = (raw or "").strip().lower()
    if not s:
        return ""
    if "://" in s:
        s = (urlsplit(s).hostname or "").lower()
    s = s.lstrip("*.").split("/", 1)[0].split(":", 1)[0]
    if "." not in s or any(s.endswith(g) for g in _DOMAIN_SUFFIX_GOV):
        return ""
    return s


def extract_pivots(
    outcomes: dict[str, Outcome],
    *,
    seed_domain: str | None = None,
) -> dict[str, list[str]]:
    """Extract deduplicated entity identifiers from completed `Hit` results."""
    pivots: dict[str, list[str]] = {
        "cik": [],
        "ein": [],
        "uei": [],
        "award_ids": [],
        "fara_reg_numbers": [],
        "dockets": [],
        "domains": [],
    }
    if seed_domain:
        dom = _normalize_domain(seed_domain)
        if dom:
            _add_unique(pivots["domains"], dom)

    for out in outcomes.values():
        if not isinstance(out, Hit):
            continue
        for r in out.results:
            meta = r.meta or {}
            # CIK
            if meta.get("cik"):
                _add_unique(pivots["cik"], str(meta["cik"]).zfill(10) if str(meta["cik"]).isdigit() else meta["cik"])
            for c_item in meta.get("ciks") or []:
                s = str(c_item).strip()
                if s:
                    _add_unique(pivots["cik"], s.zfill(10) if s.isdigit() else s)
            # EIN
            if meta.get("ein"):
                _add_unique(pivots["ein"], str(meta["ein"]).strip())
            # UEI
            for ukey in ("uei", "recipient_uei"):
                if meta.get(ukey):
                    _add_unique(pivots["uei"], str(meta[ukey]).upper())
            # Awards / PIIDs
            for akey in ("award_id", "piid"):
                if meta.get(akey):
                    _add_unique(pivots["award_ids"], meta[akey])
            # FARA registration number
            if meta.get("reg_number") and r.source == "fara":
                _add_unique(pivots["fara_reg_numbers"], meta["reg_number"])
            # Dockets
            if meta.get("docket_number"):
                _add_unique(pivots["dockets"], meta["docket_number"])
            # Domains from websites or SANs
            for dkey in ("website", "domain"):
                if meta.get(dkey):
                    dom = _normalize_domain(str(meta[dkey]))
                    if dom:
                        _add_unique(pivots["domains"], dom)

            blob = f"{r.url} {r.snippet}"
            for m in _CIK_URL_RE.finditer(blob):
                _add_unique(pivots["cik"], m.group(1).zfill(10))
            for m in _EIN_RE.finditer(blob):
                _add_unique(pivots["ein"], m.group(0))
            for m in _AWARD_RE.finditer(blob):
                _add_unique(pivots["award_ids"], m.group(0))
            for m in _UEI_RE.finditer(blob):
                tok = m.group(0)
                if (
                    not _AWARD_RE.fullmatch(tok)
                    and not re.fullmatch(r"\d{2}[A-Z]{2,6}\d{2}[A-Z]\d{3,8}[A-Z]?", tok)
                    and tok not in pivots["award_ids"]
                ):
                    _add_unique(pivots["uei"], tok)
            for m in _DOCKET_RE.finditer(blob):
                _add_unique(pivots["dockets"], m.group(0))

    return pivots


def run_investigation(
    query: str,
    *,
    sources: Sequence[str] | None = None,
    domain: str | None = None,
    limit: int = 5,
    store: Store | None = None,
    limiter: Limiter | None = None,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Run a coordinated multi-source sweep and automatic secondary identifier pivots."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    requested = list(sources) if sources else list(DEFAULT_SOURCES)
    outcomes: dict[str, Outcome] = {}
    for src in requested:
        outcomes[src] = run_single_source(
            src, query, limit=limit, store=store, limiter=limiter, use_cache=use_cache
        )

    clean_domain = _normalize_domain(domain) if domain else ""
    if clean_domain:
        if "crtsh" not in outcomes:
            outcomes["crtsh"] = run_single_source(
                "crtsh", clean_domain, limit=limit, store=store, limiter=limiter, use_cache=use_cache
            )
        if "wayback" not in outcomes:
            outcomes["wayback"] = run_single_source(
                "wayback", f"https://{clean_domain}/", limit=limit, store=store, limiter=limiter, use_cache=use_cache
            )

    pivots = extract_pivots(outcomes, seed_domain=clean_domain or None)
    pivot_outcomes: dict[str, Outcome] = {}

    from ..sources import crtsh, nonprofit, sec

    for ein in pivots["ein"][:3]:
        pivot_outcomes[f"nonprofit:ein:{ein}"] = nonprofit.by_ein(
            ein, store=store, limiter=limiter, use_cache=use_cache
        )
    for cik in pivots["cik"][:3]:
        pivot_outcomes[f"sec:cik:{cik}"] = sec.submissions(
            cik, limit=limit, store=store, limiter=limiter, use_cache=use_cache
        )
    already_domains = {clean_domain} if "crtsh" in outcomes and clean_domain else set()
    for dom in pivots["domains"][:2]:
        if dom not in already_domains:
            pivot_outcomes[f"crtsh:domain:{dom}"] = crtsh.search(
                dom, limit=limit, store=store, limiter=limiter, use_cache=use_cache
            )

    all_outcomes: dict[str, Outcome] = {**outcomes, **pivot_outcomes}
    hit_sources = [k for k, o in all_outcomes.items() if isinstance(o, Hit)]
    absence_sources = [k for k, o in all_outcomes.items() if isinstance(o, VerifiedAbsence)]
    incomplete_sources = {
        k: {"type": type(o).__name__, "detail": getattr(o, "detail", "")}
        for k, o in all_outcomes.items()
        if isinstance(o, (AccessBlocker, RateLimited, AwaitingHuman))
    }

    return {
        "query": query,
        "domain": clean_domain or None,
        "sources_queried": list(outcomes.keys()),
        "pivots": pivots,
        "outcomes": {k: o.to_dict() for k, o in outcomes.items()},
        "pivot_outcomes": {k: o.to_dict() for k, o in pivot_outcomes.items()},
        "summary": {
            "hit_sources": hit_sources,
            "verified_absence_sources": absence_sources,
            "incomplete_sources": incomplete_sources,
            "is_complete": not incomplete_sources,
        },
    }


def format_investigation_report(report: dict[str, Any]) -> str:
    """Format an investigation dict for human terminal consumption."""
    lines = [
        f"== Investigate == {report['query']!r}",
        f"sources queried: {', '.join(report['sources_queried'])}",
    ]
    summ = report["summary"]
    lines.append(
        f"hits: {len(summ['hit_sources'])}  |  verified absences: {len(summ['verified_absence_sources'])}  |  incomplete: {len(summ['incomplete_sources'])}"
    )

    non_empty_pivots = {k: v for k, v in (report.get("pivots") or {}).items() if v}
    if non_empty_pivots:
        lines.append("\nDiscovered identifier pivots:")
        for k, vals in non_empty_pivots.items():
            lines.append(f"  {k}: {', '.join(vals[:8])}")

    if summ["hit_sources"]:
        lines.append("\nHits:")
        for src in summ["hit_sources"]:
            out_d = report["outcomes"].get(src) or report["pivot_outcomes"].get(src) or {}
            res = out_d.get("results") or []
            lines.append(f"  [{src}] {len(res)} result(s)")
            for item in res[:3]:
                lines.append(f"    - {str(item.get('title') or '')[:100]}  ({item.get('url')})")

    if summ["verified_absence_sources"]:
        lines.append("\nBounded verified absences:")
        for src in summ["verified_absence_sources"]:
            lines.append(f"  [{src}] 0 matches")

    if summ["incomplete_sources"]:
        lines.append("\nIncomplete / blocked sources (DO NOT claim negative):")
        for src, info in summ["incomplete_sources"].items():
            lines.append(f"  [{src}] {info['type']}: {info['detail']}")

    return "\n".join(lines)
