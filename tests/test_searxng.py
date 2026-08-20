"""SearXNG adapter: their catalogue, our failure semantics.

Two things in SearXNG's source are disqualifying for a defensible negative, and
these tests pin the adapter's refusal to inherit either:

  * json_engine.py returns an EMPTY LIST on a blocked HTTP status, so a wall and
    an empty shelf look identical in `results`.
  * failures live in a parallel `unresponsive_engines` channel that an operator
    can switch off per engine via `display_error_messages`.
"""
import json
import pathlib
import tempfile

import pytest

from evidence_search.core.limits import Limiter
from evidence_search.core.results import (AccessBlocker, Hit, RateLimited,
                                         VerifiedAbsence)
from evidence_search.core.store import Store, cache_key
from evidence_search.engines import searxng


def _kit():
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    # Pre-seed /config so tests do not depend on a live instance.
    s.put(cache_key("searxng:config:v2", "http://x"), "searxng",
          ["searxng:google", "searxng:bing", "searxng:duckduckgo", "searxng:marginalia"], ttl_s=3600)
    return s, Limiter(s)


def _reply(monkeypatch, payload):
    # searxng.py binds `fetch` at import time, so patch its own reference.
    monkeypatch.setattr(searxng, "fetch", lambda *a, **k: (json.dumps(payload), None))


def test_engine_names_are_namespaced():
    """SearXNG's upstream engines share names with nothing in our ledger -- but a
    live worker could not tell them apart.

    It saw `RATE-LIMITED: brave`, ran `evidence-search limits`, saw brave at 0/20,
    and reasonably concluded the tool was contradicting itself. Our limiter
    governs calls WE make; this reports what Brave did to SearXNG. The prefix is
    the only thing that distinguishes the two namespaces.
    """
    rl, er, _ = searxng._classify([["brave", "too many requests"]])
    assert rl == ["searxng:brave"], "upstream engines must be namespaced"


def test_classification_survives_a_translated_locale():
    """The failure channel is gettext-translated PROSE, not a stable code.

    searx/webutils.py collapses `error_type` and `suspended` into one localised
    string before the JSON is built, and SearXNG ships 60 locales. Verified
    live: a de-DE Accept-Language turns "too many requests" into "zu viele
    Anfragen". We pin `locale=en`, and still recognise the common non-English
    forms in case an instance ignores it.
    """
    rl, er, unknown = searxng._classify([["brave", "zu viele Anfragen"]])
    assert rl == ["searxng:brave"] and not unknown

    rl, er, unknown = searxng._classify([["x", "trop de requêtes"]])
    assert rl == ["searxng:x"] and not unknown


def test_a_wall_outranks_a_backoff():
    """'Suspended: CAPTCHA' is both, and the wall is the actionable half.

    A CAPTCHA does not clear itself by waiting, so classifying it as merely
    rate-limited would invite a pointless retry.
    """
    rl, er, _ = searxng._classify([["startpage", "Suspended: CAPTCHA"]])
    assert "searxng:startpage" in er and rl == []

    rl, er, _ = searxng._classify([["bing", "Suspended: timeout"]])
    assert rl == ["searxng:bing"] and not er


def test_an_unreadable_failure_is_flagged_not_guessed():
    """Never silently file an unrecognised message under a guessed category.

    It still dirties coverage -- an unreadable failure is a failure -- but it
    is labelled unclassified so the signal map can be fixed.
    """
    rl, er, unknown = searxng._classify([["y", "リクエストが多すぎます"]])
    assert unknown == ["searxng:y"]
    assert "unclassified" in er["searxng:y"]
    assert rl == []


def test_unclassified_failures_still_bar_a_verified_absence(monkeypatch):
    _reply(monkeypatch, {"results": [], "unresponsive_engines": [
        ["google", "something we have never seen"]]})
    s, L = _kit()
    out = searxng.search("q", base="http://x", store=s, limiter=L)
    assert isinstance(out, RateLimited)
    assert not isinstance(out, VerifiedAbsence)


def test_search_pins_the_locale(monkeypatch):
    """Without this the instance answers in whatever the request negotiated."""
    seen = {}

    def spy(url, **kw):
        seen["url"] = url
        seen["headers"] = kw.get("headers") or {}
        return json.dumps({"results": [], "unresponsive_engines": []}), None

    monkeypatch.setattr(searxng, "fetch", spy)
    s, L = _kit()
    searxng.search("q", base="http://x", store=s, limiter=L)
    assert "locale=en" in seen["url"]
    assert "en" in seen["headers"].get("Accept-Language", "")


def test_partial_sweep_cannot_certify_absence(monkeypatch):
    """THE guarantee, extended across the federated tier.

    Zero results while 2 of 4 engines were throttled is not evidence of
    absence -- it is a tooling-limited negative.
    """
    _reply(monkeypatch, {"results": [], "unresponsive_engines": [
        ["google", "too many requests"], ["bing", "Suspended: timeout"]]})
    s, L = _kit()
    out = searxng.search("q", base="http://x", store=s, limiter=L)
    assert isinstance(out, RateLimited)
    assert not isinstance(out, VerifiedAbsence)
    assert set(out.coverage.rate_limited) == {"searxng:google", "searxng:bing"}


def test_clean_sweep_yields_a_publishable_absence(monkeypatch):
    _reply(monkeypatch, {"results": [], "unresponsive_engines": []})
    s, L = _kit()
    out = searxng.search("q", base="http://x", store=s, limiter=L)
    assert isinstance(out, VerifiedAbsence)
    assert out.coverage.is_clean
    assert len(out.coverage.queried) == 4, "coverage must name every enabled engine"


def test_a_blocked_engine_is_errored_not_merely_absent(monkeypatch):
    """CAPTCHA and access-denied are walls, and must dirty coverage."""
    _reply(monkeypatch, {"results": [], "unresponsive_engines": [
        ["google", "CAPTCHA"], ["bing", "access denied"]]})
    s, L = _kit()
    out = searxng.search("q", base="http://x", store=s, limiter=L)
    assert isinstance(out, RateLimited)      # downgraded, never certified
    assert set(out.coverage.errored) == {"searxng:google", "searxng:bing"}


def test_coverage_counts_engines_that_were_never_asked(monkeypatch):
    """`responsive` is queried minus failed -- not merely whoever returned rows.

    Without /config, zero results is uninterpretable: it cannot distinguish
    'nobody found it' from 'nobody was asked'.
    """
    _reply(monkeypatch, {
        "results": [{"url": "https://a/x", "title": "t", "engines": ["marginalia"]}],
        "unresponsive_engines": [["google", "too many requests"]]})
    s, L = _kit()
    out = searxng.search("q", base="http://x", store=s, limiter=L)
    assert isinstance(out, Hit)
    assert set(out.coverage.queried) == {"searxng:google", "searxng:bing", "searxng:duckduckgo", "searxng:marginalia"}
    assert "searxng:google" not in out.coverage.responsive
    assert set(out.coverage.responsive) == {"searxng:bing", "searxng:duckduckgo", "searxng:marginalia"}


def test_single_engine_finds_are_marked_not_buried(monkeypatch):
    """SearXNG scores by consensus (weight * len(positions)), which demotes the
    obscure result only one index carries. On this beat that is often the find."""
    _reply(monkeypatch, {"results": [
        {"url": "https://a/x", "title": "everyone", "engines": ["google", "bing"]},
        {"url": "https://b/y", "title": "only marginalia", "engines": ["marginalia"]},
    ], "unresponsive_engines": []})
    s, L = _kit()
    out = searxng.search("q", base="http://x", store=s, limiter=L)
    uniq = [r for r in out.results if r.unique_to_engine]
    assert [r.title for r in uniq] == ["only marginalia"]


def test_a_partial_sweep_is_never_cached(monkeypatch):
    """Caching a degraded search would replay it as though all engines answered."""
    _reply(monkeypatch, {
        "results": [{"url": "https://a/x", "title": "t", "engines": ["bing"]}],
        "unresponsive_engines": [["google", "too many requests"]]})
    s, L = _kit()
    searxng.search("q", base="http://x", store=s, limiter=L)
    assert s.get_entry(cache_key("searxng", "q", categories="general",
                                 pageno=1, base="http://x")) is None


def test_html_response_explains_the_json_format_switch(monkeypatch):
    """SearXNG ships with the JSON API disabled; say so instead of 'parse error'."""
    monkeypatch.setattr(searxng, "fetch", lambda *a, **k: ("<html>results</html>", None))
    s, L = _kit()
    out = searxng.search("q", base="http://x", store=s, limiter=L)
    assert isinstance(out, AccessBlocker)
    assert "formats" in out.detail and "json" in out.detail


def test_web_absence_is_labelled_as_a_weak_negative(monkeypatch):
    """A zero from a fuzzy tier must not read like a corpus-backed absence.

    SearXNG's engine model declares paging, time_range_support, safesearch and
    language_support -- and NOTHING about exact-phrase support. There is no way
    to ask an engine whether it honours quotes, and observation says most do
    not: `"<person-h>"` returned 26 results against 28 unquoted, same
    near-miss profiles on top.

    We deliberately do not filter locally to compensate. Filtering one page of
    an N-page set yields a number that looks like a count and is not one -- the
    CourtListener page-cap bug in a new place. Better to say the tier is fuzzy.
    """
    _reply(monkeypatch, {"results": [], "unresponsive_engines": []})
    s_, L = _kit()
    out = searxng.search('"<person-h>"', base="http://x", store=s_, limiter=L)
    assert isinstance(out, VerifiedAbsence)
    # The warning now lives in STRUCTURED fields rather than a prose blob, so a
    # caller can act on it instead of grepping a sentence.
    assert out.is_absolute is False, "a fuzzy tier can never yield an absolute negative"
    assert any("quoted phrases" in c for c in out.caveats)
    assert out.probes and out.probes[0].exact_match_supported is False
    assert out.not_searched, "must state what it did not cover"


def test_no_local_exact_filtering():
    """Guard against reintroducing it.

    Local filtering is not a substitute for upstream query semantics, and on a
    counting task it actively manufactures a wrong figure.
    """
    from evidence_search.engines import searxng as m
    assert m.EXACT_PHRASE_SUPPORTED is False
    # Guard the BEHAVIOUR, not the source text: a string check tripped on the
    # legitimate `exact_match_supported` provenance field once that was added.
    import inspect
    src = inspect.getsource(m.search)
    for banned in ("_exact_terms(", "exact_filtered", "if exact:"):
        assert banned not in src, f"local exact-filtering reintroduced via {banned}"


# --- dedup on normalized url --------------------------------------------------

def _clean(results):
    return {"results": results, "unresponsive_engines": []}


def test_same_page_under_tracking_params_folds_to_one(monkeypatch):
    """SearXNG hashes results with no url normalization, so one page recurs
    under utm_* and reads as independent corroboration."""
    s, L = _kit()
    _reply(monkeypatch, _clean([
        {"url": "https://www.example.gov/report?utm_source=news", "title": "Report",
         "content": "first", "engines": ["google"], "positions": [1]},
        {"url": "https://example.gov/report/", "title": "Report",
         "content": "second", "engines": ["bing"], "positions": [3]},
    ]))
    out = searxng.search("q", store=s, limiter=L, base="http://x", use_cache=False)
    assert isinstance(out, Hit)
    assert len(out.results) == 1, "the same page was counted twice"


def test_dedup_merges_provenance_rather_than_dropping_it(monkeypatch):
    """The engine list is evidence -- unique_to_engine is computed from it -- so
    folding must union the engines, not keep whichever copy arrived first."""
    s, L = _kit()
    _reply(monkeypatch, _clean([
        {"url": "https://www.example.gov/report?utm_source=x", "title": "Report",
         "content": "first", "engines": ["google"], "positions": [1]},
        {"url": "https://example.gov/report", "title": "Report",
         "content": "second", "engines": ["bing"], "positions": [3]},
    ]))
    r = searxng.search("q", store=s, limiter=L, base="http://x", use_cache=False).results[0]
    assert r.engines == ["bing", "google"]
    assert not r.unique_to_engine, "a two-engine find must not read as single-engine"
    assert r.rank_by_engine == {"google": 1, "bing": 3}


def test_distinct_pages_are_not_folded(monkeypatch):
    s, L = _kit()
    _reply(monkeypatch, _clean([
        {"url": "https://example.gov/a", "title": "A", "engines": ["google"]},
        {"url": "https://example.gov/b", "title": "B", "engines": ["google"]},
    ]))
    assert len(searxng.search("q", store=s, limiter=L, base="http://x",
                              use_cache=False).results) == 2


def test_dedup_does_not_clobber_the_cache_key(monkeypatch):
    """Regression: the fold key was briefly named `key`, shadowing the cache key
    this function writes under -- so a clean sweep cached itself under the last
    result's URL and was never replayable."""
    s, L = _kit()
    payload = _clean([{"url": "https://example.gov/a", "title": "A", "engines": ["google"]}])
    _reply(monkeypatch, payload)
    searxng.search("q", store=s, limiter=L, base="http://x", use_cache=True)
    expected = cache_key("searxng", "q", categories="general", pageno=1, base="http://x")
    assert s.get(expected) is not None, "clean sweep was not cached under its cache key"


def test_fold_keeps_the_best_rank_not_the_first_seen(monkeypatch):
    """setdefault kept whichever duplicate arrived first, so a tracking-param
    copy at position 9 masked the same engine surfacing the page at 1."""
    s, L = _kit()
    _reply(monkeypatch, _clean([
        {"url": "https://ex.gov/r?utm_source=a", "title": "R",
         "engines": ["google"], "positions": [9]},
        {"url": "https://ex.gov/r", "title": "R",
         "engines": ["google"], "positions": [1]},
    ]))
    r = searxng.search("q", store=s, limiter=L, base="http://x", use_cache=False).results[0]
    assert r.rank_by_engine == {"google": 1}


def test_duplicates_without_engine_metadata_do_not_crash(monkeypatch):
    """`len(positions) == len(engines)` is satisfied when BOTH are empty, and
    indexing r["positions"] then raised KeyError on the merge path."""
    s, L = _kit()
    _reply(monkeypatch, _clean([
        {"url": "https://ex.gov/z"}, {"url": "https://ex.gov/z/"},
    ]))
    out = searxng.search("q", store=s, limiter=L, base="http://x", use_cache=False)
    assert isinstance(out, Hit) and len(out.results) == 1


def test_fold_records_that_it_folded(monkeypatch):
    """meta/score are kept from the first copy only, so the Result must at
    least say it stands for more than one upstream row."""
    s, L = _kit()
    _reply(monkeypatch, _clean([
        {"url": "https://ex.gov/r?utm_source=a", "title": "R",
         "engines": ["google"], "searxng_score": 0.1},
        {"url": "https://ex.gov/r", "title": "R",
         "engines": ["bing"], "searxng_score": 9.9},
    ]))
    r = searxng.search("q", store=s, limiter=L, base="http://x", use_cache=False).results[0]
    assert r.meta["folded_from"] == 2


def test_unfolded_results_are_not_marked_as_folded(monkeypatch):
    s, L = _kit()
    _reply(monkeypatch, _clean([{"url": "https://ex.gov/a", "engines": ["google"]}]))
    r = searxng.search("q", store=s, limiter=L, base="http://x", use_cache=False).results[0]
    assert "folded_from" not in r.meta


def test_a_refused_cache_write_still_serves_the_results(monkeypatch):
    """CachePoisoned propagated out of searxng and crashed the call.

    Refusing to cache is right, but the caller still asked a question. And the
    refusal must NOT touch coverage: marking it errored would flip is_clean and
    downgrade a legitimate absence to RateLimited, conflating a storage problem
    with a retrieval one -- the precise confusion this package prevents.
    """
    from evidence_search.core.store import CachePoisoned
    s, L = _kit()
    _reply(monkeypatch, _clean([
        {"url": f"https://ex.gov/{i}", "title": "T", "engines": ["google"]}
        for i in range(4)]))

    real_put = s.put
    def refusing_put(key, source, payload, **kw):
        if not kw.get("is_metadata"):
            raise CachePoisoned("simulated parse-failure payload")
        return real_put(key, source, payload, **kw)
    monkeypatch.setattr(s, "put", refusing_put)

    out = searxng.search("q", store=s, limiter=L, base="http://x", use_cache=True)
    assert isinstance(out, Hit)
    assert len(out.results) == 4, "results the caller asked for were dropped"
    assert out.coverage.cache_write_refused
    assert out.coverage.is_clean, "a storage problem must not dirty coverage"
