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

from cascade_search.core.limits import Limiter
from cascade_search.core.results import (AccessBlocker, Hit, RateLimited,
                                         VerifiedAbsence)
from cascade_search.core.store import Store, cache_key
from cascade_search.engines import searxng


def _kit():
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    # Pre-seed /config so tests do not depend on a live instance.
    s.put(cache_key("searxng:config", "http://x"), "searxng",
          ["google", "bing", "duckduckgo", "marginalia"], ttl_s=3600)
    return s, Limiter(s)


def _reply(monkeypatch, payload):
    # searxng.py binds `fetch` at import time, so patch its own reference.
    monkeypatch.setattr(searxng, "fetch", lambda *a, **k: (json.dumps(payload), None))


def test_classification_survives_a_translated_locale():
    """The failure channel is gettext-translated PROSE, not a stable code.

    searx/webutils.py collapses `error_type` and `suspended` into one localised
    string before the JSON is built, and SearXNG ships 60 locales. Verified
    live: a de-DE Accept-Language turns "too many requests" into "zu viele
    Anfragen". We pin `locale=en`, and still recognise the common non-English
    forms in case an instance ignores it.
    """
    rl, er, unknown = searxng._classify([["brave", "zu viele Anfragen"]])
    assert rl == ["brave"] and not unknown

    rl, er, unknown = searxng._classify([["x", "trop de requêtes"]])
    assert rl == ["x"] and not unknown


def test_a_wall_outranks_a_backoff():
    """'Suspended: CAPTCHA' is both, and the wall is the actionable half.

    A CAPTCHA does not clear itself by waiting, so classifying it as merely
    rate-limited would invite a pointless retry.
    """
    rl, er, _ = searxng._classify([["startpage", "Suspended: CAPTCHA"]])
    assert "startpage" in er and rl == []

    rl, er, _ = searxng._classify([["bing", "Suspended: timeout"]])
    assert rl == ["bing"] and not er


def test_an_unreadable_failure_is_flagged_not_guessed():
    """Never silently file an unrecognised message under a guessed category.

    It still dirties coverage -- an unreadable failure is a failure -- but it
    is labelled unclassified so the signal map can be fixed.
    """
    rl, er, unknown = searxng._classify([["y", "リクエストが多すぎます"]])
    assert unknown == ["y"]
    assert "unclassified" in er["y"]
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
    assert set(out.coverage.rate_limited) == {"google", "bing"}


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
    assert set(out.coverage.errored) == {"google", "bing"}


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
    assert set(out.coverage.queried) == {"google", "bing", "duckduckgo", "marginalia"}
    assert "google" not in out.coverage.responsive
    assert set(out.coverage.responsive) == {"bing", "duckduckgo", "marginalia"}


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
