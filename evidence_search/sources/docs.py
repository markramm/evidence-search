"""Documentation search — the gap evidence-search had.

On 2026-08-19 this tool was asked a pricing question and returned product
announcements, because Google News RSS indexes news, not docs. The answer came
from fetching documentation URLs directly. That is a real capability gap: this
package covers courts, contracts, and disclosures, and had nothing for
"what does the vendor's own documentation say?"

Strategy: many documentation sites publish an llms.txt index (or a sitemap).
Fetch the index, match the query against page titles/paths, then fetch the best
pages. No search engine, no API key, no budget.

Registered sites are those this pipeline actually consults. Add more freely --
the only requirement is an index URL and a base.
"""
from __future__ import annotations

import re
from ..core.http import fetch as _http_fetch
from ..core.limits import Limiter
from ..core.results import AccessBlocker, Blocker, Coverage, Result
from ..core.source import RecognizedEmpty, run_source
from ..core.store import Store

SOURCE = "docs"

SITES: dict[str, dict] = {
    "claude-code": {"index": "https://code.claude.com/docs/llms.txt",
                    "base": "https://code.claude.com/docs/"},
    "claude-api":  {"index": "https://platform.claude.com/docs/llms.txt",
                    "base": "https://platform.claude.com/docs/"},
}

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")


def _score(query: str, title: str, url: str) -> int:
    """Cheap lexical scoring. Title matches beat URL matches."""
    terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
    if not terms:
        return 0
    t, u = title.lower(), url.lower()
    return sum((3 if term in t else 0) + (1 if term in u else 0) for term in terms)


def _parser(query: str, site: str, fetch_top: int, store, limiter):
    def _parse(body: str) -> list[Result]:
        links = _LINK.findall(body)
        if not links:
            # An llms.txt index is a list of markdown links. A body with none
            # is not an index -- an error page, a wall, a moved file -- and
            # says nothing about what the documentation contains.
            raise ValueError("no markdown links in the documentation index")
        scored = []
        for title, url in links:
            sc = _score(query, title, url)
            if sc:
                scored.append((sc, title.strip(), url.strip()))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return RecognizedEmpty(f"index of {len(links)} pages, none matching")

        out = []
        for rank, (sc, title, url) in enumerate(scored[:25], 1):
            r = Result(url=url, title=title, snippet=f"relevance {sc}",
                       source=f"{SOURCE}:{site}", engines=[SOURCE],
                       index_origin=["n/a"], score=float(sc),
                       meta={"site": site, "rank": rank})
            # Each body fetch is a real call and must claim its own slot. The
            # original computed a check and discarded it, then fetched anyway.
            if rank <= fetch_top:
                allowed, _, why = limiter.reserve(SOURCE)
                if not allowed:
                    r.meta["text_skipped"] = f"rate limit: {why}"
                else:
                    page, pblocked = _http_fetch(url, source=SOURCE, query=query)
                    limiter.note_outcome(SOURCE, pblocked)
                    if page and not pblocked:
                        r.meta["text"] = re.sub(r"\n{3,}", "\n\n", page)[:20000]
            out.append(r)
        return out
    return _parse


def search(query: str, site: str = "claude-code", fetch_top: int = 0,
           store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search a documentation site's index. fetch_top>0 also pulls page text."""
    store = store or Store()
    limiter = limiter or Limiter(store)

    if site not in SITES:
        # An unknown site was never searched, so it cannot be absent from it.
        # This returned a VerifiedAbsence: a typo in --site read as "the
        # documentation does not mention this". Refused the way oscn refuses
        # an unknown county.
        import difflib
        near = difflib.get_close_matches(site, sorted(SITES), n=3, cutoff=0.6)
        return AccessBlocker(
            query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "unknown-site"}),
            mechanism=Blocker.NOT_FOUND, url="",
            detail=(f"{site!r} is not a registered documentation site. "
                    + (f"Did you mean: {', '.join(near)}? " if near else "")
                    + f"Known sites: {', '.join(sorted(SITES))}. Nothing was "
                      "searched, so this is NOT an absence."))

    return run_source(
        query, source=SOURCE, url=SITES[site]["index"],
        searched=f"{site} documentation index ({SITES[site]['index']})",
        parse=_parser(query, site, fetch_top, store, limiter),
        store=store, limiter=limiter, use_cache=use_cache,
        exact_match_supported=False,
        corpus=(f"{site} documentation INDEX: page titles and URLs listed in "
                "llms.txt. Page bodies are not searched."),
        not_searched=["the text of the documentation pages themselves",
                      "pages the site does not list in its llms.txt"],
        caveats=["Lexical match on page TITLES and URLS only: a topic covered "
                 "inside a page whose title does not use these words is not found."],
        cache_params={"site": site},
    )
