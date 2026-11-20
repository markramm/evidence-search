"""GDELT DOC 2.0 global news article discovery client (`evidence-search gdelt`, Issue #6).

Queries `https://api.gdeltproject.org/api/v2/doc/doc?mode=ArtList&format=json`
with optional `domain:`, `sourcecountry:`, `sourcelang:`, and `timespan` filters.
Explicitly declared as a discovery tier (`counts=False`, `exact_match_supported=False`).
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import Result
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import Store

SOURCE = "gdelt"
API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="GDELT Project DOC 2.0 global news and broadcast-monitor index.",
    not_searched=(
        "Paywalled print/wire archives not crawled by GDELT",
        "Primary public records (court dockets, procurement filings, lobbying disclosures)",
    ),
    caveats=(
        "GDELT DOC 2.0 caps article lists at 250 records and weights machine-translated international coverage; treat results as discovery leads rather than an exhaustive media census.",
    ),
)


def _parse_gdelt(body: str) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"GDELT returned non-JSON: {e}") from e

    rows = json_rows(data, "articles", total_key=None)
    if isinstance(rows, RecognizedEmpty):
        return rows

    out: list[Result] = []
    for art in rows:
        if not isinstance(art, dict):
            continue
        url = str(art.get("url") or "").strip()
        title = str(art.get("title") or url or "(untitled article)").strip()
        domain = str(art.get("domain") or "").strip()
        seendate = str(art.get("seendate") or "").strip()
        country = str(art.get("sourcecountry") or "").strip()
        lang = str(art.get("language") or "").strip()
        out.append(Result(
            url=url or API_URL,
            title=title[:180],
            snippet=" | ".join(p for p in (
                domain,
                f"seen: {seendate}" if seendate else "",
                country,
                lang,
            ) if p)[:360],
            source=SOURCE,
            engines=[SOURCE],
            meta={
                "domain": domain,
                "seendate": seendate,
                "sourcecountry": country,
                "language": lang,
            },
        ))
    return out


def search(query: str, *, domain: str | None = None, country: str | None = None,
           language: str | None = None, timespan: str | None = None,
           limit: int = 25, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search GDELT DOC 2.0 ArtList API."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    terms = [(query or "").strip()]
    if domain:
        terms.append(f"domain:{domain.strip()}")
    if country:
        terms.append(f"sourcecountry:{country.strip()}")
    if language:
        terms.append(f"sourcelang:{language.strip()}")
    full_q = " ".join(t for t in terms if t).strip()

    params: dict[str, str] = {
        "query": full_q,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(max(1, min(limit, 250))),
    }
    if timespan:
        params["timespan"] = timespan.strip()
    url = f"{API_URL}?{urlencode(params)}"

    return run_source(
        full_q,
        source=SOURCE,
        url=url,
        parse=_parse_gdelt,
        searched=f"GDELT DOC 2.0 ({full_q})",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers={"Accept": "application/json"},
        cache_params={k: v for k, v in params.items() if k != "query"},
        exact_match_supported=False,
        corpus=CORPUS,
        not_searched=list(NOT_SEARCHED),
        caveats=list(STANDING_CAVEATS),
    )
