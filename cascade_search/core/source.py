"""One template for every source's outer shell.

Each source repeated the same ~20 lines: check cache, reserve a slot, fetch,
parse, return absence-or-hit. That duplication was not cosmetic -- it is why
`docs` computed a rate check and discarded it, and why the cached-absence
defect existed five times identically. A bug in the shell had to be fixed once
per source, and whichever bug the shell carried was copied into the next source
someone added.

Here the shell lives once. A source supplies only what is genuinely its own:
how to build the URL, and how to turn a response body into Results.
"""
from __future__ import annotations

import time
from typing import Callable

from .limits import Limiter
from .results import (Coverage, Hit, RateLimited, Result, replay_cached,
                      verified_absence)
from .store import CachePoisoned, Store, cache_key


def run_source(
    query: str,
    *,
    source: str,
    url: str,
    searched: str,
    parse: Callable[[str], list[Result]],
    store: Store | None = None,
    limiter: Limiter | None = None,
    use_cache: bool = True,
    index_origin: str = "n/a",
    cache_params: dict | None = None,
    ttl_s: int = 86400,
    headers: dict | None = None,
    verify=None,
    on_blocked=None,
):
    """Run one source end to end and return a typed Outcome.

    `parse` receives the response body and returns Results; raising
    ValueError from it means "the payload shape changed", which is reported as
    a blocked/errored outcome rather than an absence -- a parse failure is
    never evidence that a thing does not exist.

    `verify(body)` may return a refusal string to reject a response that looks
    successful but is not (e.g. an endpoint that echoes back an empty filter and
    serves its whole unfiltered index).
    """
    from .http import fetch as http_fetch
    from .results import AccessBlocker, Blocker

    store = store or Store()
    limiter = limiter or Limiter(store)
    key = cache_key(source, query, **(cache_params or {}))

    if use_cache:
        entry = store.get_entry(key)
        if entry is not None:
            return replay_cached(query, entry[0], entry[1], source=source,
                                 searched=searched, index_origin=index_origin)

    allowed, retry, why = limiter.reserve(source)
    if not allowed:
        return RateLimited(
            query=query, coverage=Coverage(queried=[source], rate_limited=[source]),
            source=source, retry_after_s=int(retry) if retry else None, detail=why)

    t0 = time.time()
    body, blocked = http_fetch(url, source=source, query=query, headers=headers)
    if blocked:
        if on_blocked is not None:
            handled = on_blocked(blocked, body)
            if handled is not None:
                return handled
        blocked.coverage = Coverage(
            queried=[source], errored={source: "blocked"},
            elapsed_ms=int((time.time() - t0) * 1000))
        return blocked

    if verify is not None and (refusal := verify(body)):
        return AccessBlocker(
            query=query,
            coverage=Coverage(queried=[source], errored={source: "query-not-applied"}),
            mechanism=Blocker.SERVER_ERROR, url=url, detail=refusal)

    try:
        results = parse(body)
    except ValueError as e:
        return AccessBlocker(
            query=query, coverage=Coverage(queried=[source], errored={source: "parse"}),
            mechanism=Blocker.SERVER_ERROR, url=url,
            detail=f"payload shape changed ({e}) -- the source may have altered its format")

    cov = Coverage(queried=[source], responsive=[source], indexes=[index_origin],
                   elapsed_ms=int((time.time() - t0) * 1000))

    if use_cache:
        try:
            store.put(key, source, [r.__dict__ for r in results], ttl_s=ttl_s)
        except CachePoisoned as e:
            # Refusing to cache is right, but the caller still asked a question.
            # Serve this run's results and let the next call re-fetch.
            #
            # This must NOT touch coverage: coverage records WHO ANSWERED, not
            # whether local storage worked. Marking it errored would flip
            # is_clean and downgrade a legitimate absence to RateLimited --
            # conflating a cache problem with a retrieval problem, which is the
            # precise confusion this package exists to prevent.
            cov.cache_write_refused = str(e)

    if not results:
        return verified_absence(query, cov, searched)
    return Hit(query=query, coverage=cov, results=results)
