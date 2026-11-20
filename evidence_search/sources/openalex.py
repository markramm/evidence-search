"""OpenAlex scholarly works, authors, institutions, grants, and retractions (`evidence-search openalex`).

Complements `crossref`:
  * Crossref is a great exact DOI resolver, but its title search is fuzzy and
    uncountable.
  * OpenAlex (`api.openalex.org/works`) indexes ~250M scholarly works with
    structured filters for author ORCIDs, institution RORs, funder grants,
    publication years, open-access PDF URLs, citation counts, and **Retraction
    Watch** flags (`is_retracted: true|false`).

Investigative use cases:
  * Vetting expert witnesses and forensic/policing methodologies cited in court
    filings: check total peer-reviewed output, citation counts, funding sources,
    and whether any work has been retracted (`--retracted-only`).
  * Any retracted work (`is_retracted=True`) automatically carries an explicit
    `entity_caveat` warning so no downstream agent cites a retracted study
    without noting the retraction.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import Result
from ..core.source import RecognizedEmpty, json_rows, run_source
from ..core.store import Store

SOURCE = "openalex"
API_URL = "https://api.openalex.org/works"
POLITE_MAILTO = "evidence-search@investigative-pipeline.local"

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="OpenAlex scholarly index (~250M works, authors, institutions, funders, and Retraction Watch flags).",
    not_searched=(
        "Proprietary court expert reports or agency consulting memos never published as scholarly works",
        "Full text of paywalled journal articles (OpenAlex indexes metadata, OA PDF links, citations, and retraction flags)",
    ),
    caveats=(
        "Author name searches without an ORCID can conflate homonymous researchers; verify institution affiliation and ORCID.",
        "Any work with `is_retracted=true` is flagged in `entity_caveat` — check the retraction notice before citing.",
    ),
)


def _parse_openalex(body: str, query: str, store: Store | None = None) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"openalex returned non-JSON: {e}") from e

    rows = json_rows(data, "results", total_key=None)
    if isinstance(rows, RecognizedEmpty):
        return rows
    meta_block = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    total_matches = meta_block.get("count")
    if not isinstance(total_matches, int):
        total_matches = len(rows)

    out: list[Result] = []
    for w in rows:
        if not isinstance(w, dict):
            continue
        oa_id = str(w.get("id") or "").strip()
        short_id = oa_id.rsplit("/", 1)[-1] if oa_id else ""
        rec_id = f"{SOURCE}:{short_id}" if short_id else None
        if store is not None and rec_id:
            try:
                store.put_record(rec_id, SOURCE, query, w)
            except Exception:
                pass
        doi = str(w.get("doi") or "").strip()
        title = str(w.get("title") or w.get("display_name") or "(untitled work)").strip()
        year = w.get("publication_year")
        pub_date = str(w.get("publication_date") or (year or ""))
        cited_by = int(w.get("cited_by_count") or 0)
        is_retracted = bool(w.get("is_retracted"))

        loc = w.get("primary_location") if isinstance(w.get("primary_location"), dict) else {}
        src = loc.get("source") if isinstance(loc.get("source"), dict) else {}
        venue = str(src.get("display_name") or "")

        oa_info = w.get("open_access") if isinstance(w.get("open_access"), dict) else {}
        oa_url = str(oa_info.get("oa_url") or loc.get("pdf_url") or "")

        authors: list[str] = []
        institutions: list[str] = []
        for auth in (w.get("authorships") or []):
            if not isinstance(auth, dict):
                continue
            a_obj = auth.get("author") if isinstance(auth.get("author"), dict) else {}
            a_name = str(a_obj.get("display_name") or "").strip()
            if a_name:
                authors.append(a_name)
            for inst in (auth.get("institutions") or []):
                if isinstance(inst, dict) and inst.get("display_name"):
                    iname = str(inst["display_name"]).strip()
                    if iname not in institutions:
                        institutions.append(iname)

        funders: list[str] = []
        for g in (w.get("grants") or []):
            if isinstance(g, dict) and g.get("funder_display_name"):
                fn = str(g["funder_display_name"]).strip()
                aid = str(g.get("award_id") or "").strip()
                funders.append(f"{fn} ({aid})" if aid else fn)

        prefix = "[RETRACTED] " if is_retracted else ""
        full_title = f"{prefix}{title}" + (f" ({year})" if year else "")
        snippet_parts = [
            f"authors: {', '.join(authors[:4])}" + (" et al." if len(authors) > 4 else "") if authors else "",
            f"venue: {venue}" if venue else "",
            f"cited_by: {cited_by}",
            f"institutions: {', '.join(institutions[:2])}" if institutions else "",
            f"funders: {', '.join(funders[:2])}" if funders else "",
            f"oa_pdf: {oa_url}" if oa_url else "",
        ]
        caveat = (
            "RETRACTED WORK: OpenAlex marks this publication as retracted (is_retracted=true). "
            "Verify the retraction notice before citing."
            if is_retracted else None
        )
        out.append(Result(
            url=doi or oa_url or oa_id or API_URL,
            title=full_title[:180],
            snippet=" | ".join(p for p in snippet_parts if p)[:360],
            source=SOURCE,
            engines=[SOURCE],
            meta={
                "record_id": rec_id,
                "openalex_id": oa_id,
                "doi": doi,
                "publication_year": year,
                "publication_date": pub_date,
                "venue": venue,
                "authors": authors,
                "institutions": institutions,
                "funders": funders,
                "cited_by_count": cited_by,
                "is_retracted": is_retracted,
                "oa_url": oa_url,
                "entity_caveat": caveat,
                "total_matches": total_matches,
            },
        ))
    return out


def search(query: str = "", *, author: str | None = None,
           institution: str | None = None, funder: str | None = None,
           doi: str | None = None, year: str | None = None,
           retracted_only: bool = False, limit: int = 25,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search OpenAlex works by query, author, institution, funder, DOI, or retraction status."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    q_clean = (query or "").strip()
    a_clean = (author or "").strip()
    i_clean = (institution or "").strip()
    f_clean = (funder or "").strip()
    d_clean = (doi or "").strip()
    y_clean = (year or "").strip()

    filters: list[str] = []
    if d_clean:
        norm_doi = d_clean if d_clean.startswith("https://doi.org/") else f"https://doi.org/{d_clean.removeprefix('doi:')}"
        filters.append(f"doi:{norm_doi}")
    if a_clean:
        if a_clean.startswith("0000-") or "orcid.org/" in a_clean:
            orcid = a_clean if a_clean.startswith("https://orcid.org/") else f"https://orcid.org/{a_clean.rsplit('/', 1)[-1]}"
            filters.append(f"authorships.author.orcid:{orcid}")
        elif a_clean.upper().startswith("A") and a_clean[1:].isdigit():
            filters.append(f"authorships.author.id:{a_clean.upper()}")
        else:
            filters.append(f"raw_author_name.search:{a_clean}")
    if i_clean:
        if "ror.org/" in i_clean:
            filters.append(f"authorships.institutions.ror:{i_clean}")
        elif i_clean.upper().startswith("I") and i_clean[1:].isdigit():
            filters.append(f"authorships.institutions.id:{i_clean.upper()}")
    if y_clean:
        filters.append(f"publication_year:{y_clean}")
    if retracted_only:
        filters.append("is_retracted:true")

    search_terms = [t for t in (
        q_clean,
        i_clean if (i_clean and "ror.org/" not in i_clean and not (i_clean.upper().startswith("I") and i_clean[1:].isdigit())) else "",
        f_clean,
    ) if t]
    combined_search = " ".join(search_terms).strip()

    params: dict[str, str] = {
        "per-page": str(max(1, min(limit, 200))),
        "mailto": POLITE_MAILTO,
    }
    if combined_search:
        params["search"] = combined_search
    if filters:
        params["filter"] = ",".join(filters)

    url = f"{API_URL}?{urlencode(params)}"
    label_parts = []
    if combined_search:
        label_parts.append(combined_search)
    if filters:
        label_parts.extend(filters)
    label = " ".join(label_parts) or "*"

    exact = bool(d_clean or (filters and not combined_search and "raw_author_name.search:" not in ",".join(filters)))
    return run_source(
        label,
        source=SOURCE,
        url=url,
        parse=lambda body: _parse_openalex(body, label, store),
        searched=f"OpenAlex ({label})",
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        headers={"Accept": "application/json"},
        cache_params=params,
        exact_match_supported=exact,
        corpus=CORPUS,
        not_searched=list(NOT_SEARCHED),
        caveats=list(STANDING_CAVEATS),
    )
