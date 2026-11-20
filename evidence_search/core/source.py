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
from .results import (Coverage, Hit, Probe, RateLimited, Result, absence_meta,
                      replay_cached, verified_absence)
from .store import CachePoisoned, Store, cache_key


class Unanswerable(ValueError):
    """The response was read, and it is not an answer to the question asked.

    Raised by a parser for a body that is well-formed but cannot support
    either a Hit or an absence: a total of 155 beside an empty page, a page
    number past the end of a listing. run_source reports it as a blocker with
    the parser's own sentence, since "payload shape changed" would misdescribe
    it.
    """


class RecognizedEmpty(list):
    """A parser's affirmative statement: "this is a valid response with zero results".

    The ONLY value a parser can return that run_source will certify as an
    absence. A plain empty list is refused, because it is also what a parser
    returns for a JSON error body, a bot-wall page served with a 200, or a
    payload whose results key was renamed -- every one of which used to be
    reported, and cached for a day, as "we searched and it is not there".

    `marker` names what was recognised (`results: []`, `count: 0`, the page's
    own no-records sentence). It is stored with the cached absence, so a
    replayed negative can only come from a response whose empty shape was
    positively identified.
    """

    def __init__(self, marker: str):
        super().__init__()
        if not marker or not str(marker).strip():
            raise ValueError("RecognizedEmpty needs the marker that was recognised")
        self.marker = str(marker)


def json_rows(data, key: str = "results", total_key: str | None = "count"):
    """Return `data[key]`, having checked that it can carry an answer.

    Raises ValueError when `data` is not an object or `key` is missing or not
    a list (an error body, a renamed field), and Unanswerable when the response
    reports a positive total beside an empty page -- that is truncation or a
    page past the end, not an absence. On a valid empty page it returns a
    RecognizedEmpty, so a parser that maps rows and returns early on
    `isinstance(rows, RecognizedEmpty)` cannot certify anything else.
    """
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    if key not in data:
        raise ValueError(
            f"no `{key}` in response (keys: {', '.join(sorted(map(str, data))) or 'none'})")
    rows = data[key]
    if not isinstance(rows, list):
        raise ValueError(f"`{key}` is {type(rows).__name__}, not a list")
    if rows:
        return rows
    total = data.get(total_key) if total_key else None
    if isinstance(total, bool):
        total = None
    if isinstance(total, (int, float)) and total > 0:
        raise Unanswerable(
            f"the response reports {total_key}={total} but this page carries no rows "
            "-- truncation or a page past the end, NOT an absence. Re-run from the "
            "first page.")
    return RecognizedEmpty(f"{key}: []" + (f", {total_key}: {total}"
                                           if total_key and total is not None else ""))


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
    escalate: bool = False,
    absent_when=None,
    answer_on: dict | None = None,
    archive_as: str | None = None,
    exact_match_supported: bool | None = None,
    corpus: str = "",
    not_searched: list[str] | None = None,
    caveats: list[str] | None = None,
    transport=None,
):
    """Run one source end to end and return a typed Outcome.

    `parse` receives the response body and returns Results; raising
    ValueError from it means "the payload shape changed", which is reported as
    a blocked/errored outcome rather than an absence -- a parse failure is
    never evidence that a thing does not exist.

    AN ABSENCE NEEDS A POSITIVE MARKER. Zero rows is not one: it is also what
    a parser yields for an error body or a wall served with a 200. A
    VerifiedAbsence is returned only when (a) `parse` returns a
    RecognizedEmpty, (b) `absent_when(body)` matches the source's own
    no-records sentinel, or (c) the source declared the response status an
    answer through `answer_on`. A parser that returns a plain empty list gets
    an AccessBlocker, so a new source cannot certify an absence by omission.

    `answer_on` maps a Blocker mechanism to the caveat that goes with reading
    it as an answer, e.g. {Blocker.NOT_FOUND: "..."} for an API that 404s a
    zero-result query. Every other non-2xx status is a blocker. Such an absence
    carries the same probes, not_searched and caveats as any other, is not
    counted as a failure toward backoff, and is never cached: nothing in the
    response distinguishes it from a moved endpoint.

    `verify(body)` may return a refusal string to reject a response that looks
    successful but is not (e.g. an endpoint that echoes back an empty filter and
    serves its whole unfiltered index).

    `escalate=True` retries a browser-passable block in a real browser, and
    surfaces an AwaitingHuman gate if the browser cannot clear it either. The
    resulting coverage names BOTH attempts, so a reader can see the plain fetch
    and the browser both failed rather than assuming one path was never tried.

    `absent_when(body)` recognises a source's own "nothing here" sentinel. OSCN
    answers an empty docket with the words "Found No Records" in an otherwise
    normal page: that is a publishable negative, and distinguishing it from a
    parser that simply found no rows is the difference between a finding and a
    silent failure. For an HTML source it is the positive marker; without it,
    or a RecognizedEmpty from the parser, there is no route to an absence.

    `archive_as(query)` returns a filename; the fetched bytes are archived with
    a SHA-256 on retrieval, never as an afterthought.

    `transport(url, source=, query=, headers=)` replaces the default GET,
    returning the same (payload, outcome_or_None) pair. USAspending is a
    POST-body API, and before this it had to re-implement the whole shell --
    cache, reserve, absence, backoff -- around one differently-shaped request.
    Everything except the request itself is identical, so only the request is
    swapped. A transport that returns a non-str payload skips archiving, which
    expects bytes or text.
    """
    from .http import fetch as http_fetch
    from .results import AccessBlocker, Blocker

    request = transport or http_fetch

    store = store or Store()
    limiter = limiter or Limiter(store)
    key = cache_key(source, query, **(cache_params or {}))

    if use_cache:
        entry = store.get_entry(key, with_meta=True)
        if entry is not None:
            replayed = replay_cached(query, entry[0], entry[1], source=source,
                                     searched=searched, index_origin=index_origin,
                                     meta=entry[2])
            # None: a cached empty result with no shape marker, written before
            # absences needed one. It is not evidence; ask again.
            if replayed is not None:
                return replayed

    allowed, retry, why = limiter.reserve(source)
    if not allowed:
        return RateLimited(
            query=query, coverage=Coverage(queried=[source], rate_limited=[source]),
            source=source, retry_after_s=int(retry) if retry else None, detail=why)

    t0 = time.time()
    body, blocked = request(url, source=source, query=query, headers=headers)
    queried = [source]

    # Feed the escalating-backoff counter. A source that keeps failing should be
    # retried on a widening interval rather than on the same schedule as a
    # healthy one; a success clears the streak, so this punishes a RUN of
    # failures, never a blip.
    #
    # A status the source declared an ANSWER is not a failure. Counting it as
    # one put federal_register into backoff for every agent after a run of
    # legitimate zero-result searches.
    answered = (blocked is not None and bool(answer_on)
                and getattr(blocked, "mechanism", None) in answer_on)
    limiter.note_outcome(source, None if answered else blocked)

    def _probe(n: int) -> list[Probe]:
        """One probe per endpoint hit -- the auditable record of the question."""
        return [Probe(source=source, endpoint=url, query=query,
                      params=dict(cache_params or {}), corpus=corpus or searched,
                      result_count=n, exact_match_supported=exact_match_supported,
                      at=time.time())]

    if answered:
        return verified_absence(
            query,
            Coverage(queried=queried, responsive=[source], indexes=[index_origin],
                     elapsed_ms=int((time.time() - t0) * 1000)),
            searched, probes=_probe(0), not_searched=not_searched,
            caveats=list(caveats or []) + [answer_on[blocked.mechanism]])

    # A browser-passable wall is what the browser tier exists for. If the
    # browser cannot clear it either, its AwaitingHuman gate is returned as-is:
    # a gate is a handoff, not a dead end.
    if blocked and escalate and getattr(blocked, "escalate_to_browser", False):
        from . import browser as _browser
        queried.append(f"{source}:browser")
        # The browser sends a SECOND request to the same host, and it used to
        # go out unreserved: OSCN's session cap exists to stay under a
        # Turnstile threshold that counts these fetches like any other.
        #
        # Spacing is waited out (the plain fetch was a moment ago, so the
        # claim would otherwise always be refused), and the failure streak is
        # not consulted: this retry is the remedy for the failure just
        # recorded. A budget window, the session cap and a shared 429 block
        # all still refuse it.
        b_ok, b_retry, b_why = limiter.reserve(
            source, wait_s=Limiter.MAX_SPACING_WAIT_S, honor_backoff=False)
        if not b_ok:
            return RateLimited(
                query=query,
                coverage=Coverage(queried=queried, errored={source: "blocked"},
                                  rate_limited=[f"{source}:browser"],
                                  elapsed_ms=int((time.time() - t0) * 1000)),
                source=source, retry_after_s=int(b_retry) if b_retry else None,
                detail=(f"The plain fetch was blocked ({blocked.mechanism.value}) and the "
                        f"browser retry was refused a slot: {b_why}. Neither attempt "
                        "read the source."))
        b_body, b_out = _browser.fetch(url, source=source, query=query, store=store)
        if b_body:
            body, blocked = b_body, None
        elif b_out is not None:
            b_out.coverage = Coverage(
                queried=queried,
                errored={source: "blocked", f"{source}:browser": "blocked"},
                elapsed_ms=int((time.time() - t0) * 1000))
            return b_out

    if blocked:
        if on_blocked is not None:
            handled = on_blocked(blocked, body)
            if handled is not None:
                return handled
        blocked.coverage = Coverage(
            queried=queried,
            errored={s_: "blocked" for s_ in queried},
            elapsed_ms=int((time.time() - t0) * 1000))
        return blocked

    if verify is not None and (refusal := verify(body)):
        return AccessBlocker(
            query=query,
            coverage=Coverage(queried=[source], errored={source: "query-not-applied"}),
            mechanism=Blocker.SERVER_ERROR, url=url, detail=refusal)

    sentinel = bool(absent_when is not None and absent_when(body))
    results: list = []
    if not sentinel:
        try:
            results = parse(body)
        except Unanswerable as e:
            return AccessBlocker(
                query=query,
                coverage=Coverage(queried=queried, errored={source: "not-an-answer"}),
                mechanism=Blocker.SERVER_ERROR, url=url, detail=str(e))
        except ValueError as e:
            return AccessBlocker(
                query=query, coverage=Coverage(queried=queried, errored={source: "parse"}),
                mechanism=Blocker.SERVER_ERROR, url=url,
                detail=f"payload shape changed ({e}) -- the source may have altered its format")

        if not results and not isinstance(results, RecognizedEmpty):
            # Zero rows and nothing that says "zero results". An error body, a
            # bot wall served with a 200 and a renamed results key all land
            # here, and all of them used to be certified -- and cached -- as
            # absences.
            return AccessBlocker(
                query=query,
                coverage=Coverage(queried=queried, errored={source: "unrecognised-response"}),
                mechanism=Blocker.SERVER_ERROR, url=url,
                detail=("the response yielded no rows and did not match this source's "
                        "known zero-results shape. It may be an error page, a challenge "
                        "served with a 200, or a changed format. NOT an absence."))

    if archive_as and isinstance(body, (str, bytes, bytearray)):
        # A custom transport may hand back already-decoded JSON; archiving wants
        # the bytes as served, and inventing a serialisation would archive
        # something the source never actually sent.
        from .archive import archive as _archive
        _archive(body.encode() if isinstance(body, str) else body,
                 archive_as, url, f"evidence-search:{source}")

    cov = Coverage(queried=queried, responsive=[source], indexes=[index_origin],
                   elapsed_ms=int((time.time() - t0) * 1000))

    # The source's own sentinel for "nothing here" -- distinct from a parser
    # that found no rows, which may mean the page shape changed.
    if not results:
        marker = "absent_when sentinel" if sentinel else results.marker
        if use_cache:
            store.put(key, source, [], ttl_s=ttl_s,
                      meta=absence_meta(_probe(0), not_searched, caveats, marker))
        return verified_absence(query, cov, searched, probes=_probe(0),
                                not_searched=not_searched, caveats=caveats)

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

    return Hit(query=query, coverage=cov, results=list(results),
               probes=_probe(len(results)))
