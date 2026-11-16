"""An absence needs a positive marker. Zero parsed rows is not one.

Every case here was reproduced in review against the code as it stood: a
response that was not an answer -- an error body, a wall served with a 200, a
renamed key, a page past the end -- parsed to zero rows, and zero rows was
returned as a VerifiedAbsence and cached for up to a day. `absent_when` was
decorative: with or without it, an empty parse was an absence.

The transport is stubbed at httpx, below `core.http.fetch`, so status handling
and blocker detection are exercised as they run in production. Nothing here
touches the network.
"""
from __future__ import annotations

import json

import httpx
import pytest

from evidence_search.core.limits import Limiter
from evidence_search.core.results import (AccessBlocker, AwaitingHuman, Blocker, Hit,
                                         RateLimited, VerifiedAbsence)
from evidence_search.core.store import Store, cache_key


class _Resp:
    def __init__(self, code, text, headers=None):
        self.status_code = code
        self.text = text
        self.headers = headers or {}
        self.content = text.encode()


@pytest.fixture
def kit(tmp_path):
    s = Store(tmp_path / "t.db")
    return s, Limiter(s)


@pytest.fixture
def serve(monkeypatch):
    """Make every GET return this status and body."""
    def _serve(code, text, headers=None):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(code, text, headers))
    return _serve


def _not_absence(out):
    assert not isinstance(out, VerifiedAbsence), f"false absence: {out.claim()}"
    assert isinstance(out, (AccessBlocker, RateLimited, AwaitingHuman)), type(out)
    return out


CHALLENGE_200 = (
    "<html><head><title>Just a moment...</title></head><body>"
    + "<p>Verifying you are human. This may take a few seconds. oscn.net needs to "
      "review the security of your connection before proceeding.</p>" * 30
    + "</body></html>")


# --- the shell ------------------------------------------------------------------

def test_every_unlisted_non_2xx_status_is_a_blocker():
    """Only 401/403/404/5xx were. A 400 with a JSON body flowed on as content."""
    from evidence_search.core.http import detect_blocker
    for code in (400, 405, 409, 410, 422, 451, 302, 304):
        assert detect_blocker(code, '{"detail": "x"}') is not None, code
    assert detect_blocker(200, "<html><body>" + "text " * 300 + "</body></html>") is None


def test_an_unshaped_cached_empty_row_is_not_replayed(kit, serve):
    """Rows cached by the old path carry no shape marker and may be error pages.

    They must be treated as a miss and re-asked, not replayed as an absence.
    """
    from evidence_search.engines import news_rss
    s, L = kit
    s.put(cache_key("news_rss", "acme"), "news_rss", [], ttl_s=3600)   # legacy shape
    serve(200, '<rss><channel><item><title>Acme</title><link>u</link></item></channel></rss>')
    out = news_rss.search("acme", store=s, limiter=L)
    assert isinstance(out, Hit), "the stale empty row was replayed instead of re-asked"


def test_a_refused_response_is_never_cached(kit, serve):
    from evidence_search.sources import courtlistener as cl
    s, L = kit
    serve(200, '{"detail": "Invalid cursor"}')
    _not_absence(cl.search('"Acme Holdings LLC"', store=s, limiter=L))
    key = cache_key("courtlistener", '"Acme Holdings LLC"', kind="r", court=None, cursor=None)
    assert s.get_entry(key) is None, "a non-answer was written to the cache"


def test_a_cached_absence_replays_with_its_probes_and_caveats(kit, serve):
    from evidence_search.sources import courtlistener as cl
    s, L = kit
    serve(200, '{"count": 0, "next": null, "results": []}')
    first = cl.search("acme holdings", store=s, limiter=L)
    again = cl.search("acme holdings", store=s, limiter=L)
    assert isinstance(first, VerifiedAbsence) and isinstance(again, VerifiedAbsence)
    assert again.coverage.cache_hits == 1
    assert [p.query for p in again.probes] == ["acme holdings"]
    assert again.not_searched == first.not_searched
    assert again.caveats == first.caveats and again.caveats


# --- CourtListener --------------------------------------------------------------

def test_courtlistener_error_body_is_not_an_absence(kit, serve):
    from evidence_search.sources import courtlistener as cl
    s, L = kit
    serve(400, '{"detail": "Invalid cursor"}')
    _not_absence(cl.search('"Acme Holdings LLC"', store=s, limiter=L, use_cache=False))
    serve(200, '{"detail": "Invalid cursor"}')
    out = _not_absence(cl.search('"Acme Holdings LLC"', store=s, limiter=L, use_cache=False))
    assert "results" in out.detail


def test_courtlistener_count_with_an_empty_page_is_not_an_absence(kit, serve):
    """`count: 155` and no rows is a paging failure, not 'no dockets'."""
    from evidence_search.sources import courtlistener as cl
    s, L = kit
    serve(200, '{"count": 155, "next": null, "results": []}')
    out = _not_absence(cl.search('"Acme Holdings LLC"', store=s, limiter=L, use_cache=False))
    assert "155" in out.detail


def test_courtlistener_real_zero_is_still_an_absence(kit, serve):
    from evidence_search.sources import courtlistener as cl
    s, L = kit
    serve(200, '{"count": 0, "next": null, "previous": null, "results": []}')
    out = cl.search('"Acme Holdings LLC"', store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert out.probes and out.not_searched


# --- Federal Register -----------------------------------------------------------

def test_fedreg_error_body_is_not_an_absence(kit, serve):
    from evidence_search.sources import federal_register as fr
    s, L = kit
    serve(400, '{"errors": {"agencies": "bad agency slug"}}')
    _not_absence(fr.search("parole", agency="Department of Homeland Security",
                           store=s, limiter=L, use_cache=False))
    serve(200, '{"errors": {"agencies": "bad agency slug"}}')
    _not_absence(fr.search("parole", agency="Department of Homeland Security",
                           store=s, limiter=L, use_cache=False))


def test_fedreg_page_past_the_end_is_not_an_absence(kit, serve):
    from evidence_search.sources import federal_register as fr
    s, L = kit
    serve(200, '{"count": 45, "total_pages": 3, "results": []}')
    out = _not_absence(fr.search("parole", page=9, store=s, limiter=L, use_cache=False))
    assert "45" in out.detail and "page" in out.detail.lower()


@pytest.mark.parametrize("body", ['{"count": 0, "results": []}',
                                  '{"count": 0, "description": "Documents matching x"}'])
def test_fedreg_zero_count_is_an_absence(kit, serve, body):
    """`count: 0` is the marker, whether or not an empty `results` rides along."""
    from evidence_search.sources import federal_register as fr
    s, L = kit
    serve(200, body)
    assert isinstance(fr.search("zzz", store=s, limiter=L, use_cache=False), VerifiedAbsence)


def test_fedreg_404_absence_carries_its_scope(kit, serve):
    """The premise (FR 404s a zero-result search) is unchanged. What changes is
    that the absence says what was asked and that it rests on a 404."""
    from evidence_search.sources import federal_register as fr
    s, L = kit
    serve(404, "<html>Not Found</html>")
    out = fr.search("parole", store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert out.claim() != "Not found"
    assert [p.query for p in out.probes] == ["parole"]
    assert out.not_searched
    assert any("404" in c for c in out.caveats)


def test_fedreg_404_answers_do_not_put_the_source_into_backoff(kit, serve):
    """A run of legitimate zero-result searches locked every agent out."""
    from evidence_search.sources import federal_register as fr
    s, L = kit
    serve(404, "{}")
    for i in range(6):
        out = fr.search(f"nonexistent term {i}", store=s, limiter=L, use_cache=False)
        assert isinstance(out, VerifiedAbsence), (i, type(out).__name__,
                                                  getattr(out, "detail", ""))
    assert L.backoff_remaining("federal_register")[0] == 0


def test_a_404_is_an_answer_only_where_declared(kit, serve):
    from evidence_search.sources import courtlistener as cl
    s, L = kit
    serve(404, "<html>Not Found</html>")
    out = _not_absence(cl.search("acme", store=s, limiter=L, use_cache=False))
    assert out.mechanism is Blocker.NOT_FOUND


# --- USAspending ----------------------------------------------------------------

@pytest.mark.parametrize("entry", ["counts", "search", "dollar_sum"])
@pytest.mark.parametrize("payload", [{"messages": ["something else"]}, {},
                                     {"results": None}, {"detail": "error"}])
def test_usaspending_payload_without_results_is_not_an_absence(kit, monkeypatch,
                                                              entry, payload):
    from evidence_search.sources import usaspending as us
    s, L = kit
    monkeypatch.setattr(us, "_post", lambda p, b, timeout=45.0: (payload, None))
    kw = {} if entry == "dollar_sum" else {"use_cache": False}
    _not_absence(getattr(us, entry)("Acme Holdings", store=s, limiter=L, **kw))


def test_usaspending_empty_page_past_the_first_is_not_an_absence(kit, monkeypatch):
    from evidence_search.sources import usaspending as us
    s, L = kit
    monkeypatch.setattr(us, "_post", lambda p, b, timeout=45.0: (
        {"results": [], "page_metadata": {"hasNext": False}}, None))
    _not_absence(us.search("Acme Holdings", page=4, store=s, limiter=L, use_cache=False))
    assert isinstance(us.search("Acme Holdings", page=1, store=s, limiter=L,
                                use_cache=False), VerifiedAbsence)


# --- ProPublica -----------------------------------------------------------------

def _sveltekit(top: dict, *rest):
    return json.dumps({"nodes": [{"type": "data", "data": [top, *rest]}]})


def test_propublica_renamed_result_key_is_not_an_absence(kit, serve):
    from evidence_search.sources import propublica_disclosures as pp
    s, L = kit
    serve(200, _sveltekit({"q": 1, "hits": 2}, "blue owl", []))
    out = _not_absence(pp.search("blue owl", store=s, limiter=L, use_cache=False))
    assert "result" in out.detail


def test_propublica_empty_result_list_is_an_absence(kit, serve):
    from evidence_search.sources import propublica_disclosures as pp
    s, L = kit
    serve(200, _sveltekit({"q": 1, "result": 2}, "blue owl", []))
    assert isinstance(pp.search("blue owl", store=s, limiter=L, use_cache=False),
                      VerifiedAbsence)


# --- OSCN -----------------------------------------------------------------------

def test_oscn_200_challenge_page_is_not_an_absence(kit, serve):
    from evidence_search.sources import oscn
    s, L = kit
    serve(200, CHALLENGE_200)
    out = _not_absence(oscn.search("caddo", lname="frazier", store=s, limiter=L,
                                   escalate=False))
    assert isinstance(out, AccessBlocker) and out.mechanism is Blocker.CLOUDFLARE
    key = cache_key("oscn", "frazier caddo", county="caddo", lname="frazier",
                    fname="", year=None)
    assert s.get_entry(key) is None


def test_oscn_unrecognised_page_is_not_an_absence(kit, serve):
    """No case links and no 'Found No Records': not the page we know."""
    from evidence_search.sources import oscn
    s, L = kit
    serve(200, "<html><body>" + "<p>Scheduled maintenance tonight.</p>" * 40 + "</body></html>")
    _not_absence(oscn.search("caddo", lname="frazier", store=s, limiter=L,
                             use_cache=False, escalate=False))


def test_oscn_sentinel_is_still_an_absence(kit, serve):
    from evidence_search.sources import oscn
    s, L = kit
    serve(200, "<html><body><p>Found No Records</p></body></html>")
    out = oscn.search("caddo", lname="frazier", store=s, limiter=L, escalate=False)
    assert isinstance(out, VerifiedAbsence)


def test_browser_gate_recognises_the_interstitial_wording():
    """The escalated path returned this page as HTML, and it cached a 24h absence."""
    from evidence_search.core import browser
    assert browser._HUMAN_GATE.search("Verifying you are human. This may take a few seconds.")
    assert browser._HUMAN_GATE.search(CHALLENGE_200[:20000])
    assert browser._GATE_TITLE.search("Just a moment...")
    assert not browser._HUMAN_GATE.search("<html><body>Found No Records</body></html>")


def test_oscn_escalated_challenge_html_is_not_cached_as_absence(kit, monkeypatch):
    """Even if the browser tier hands the wall back as HTML, it is not a finding."""
    import evidence_search.core.browser as browser
    import evidence_search.core.http as http
    from evidence_search.sources import oscn
    s, L = kit
    monkeypatch.setattr(http, "fetch", lambda url, **k: (
        None, AccessBlocker(query="q", mechanism=Blocker.TURNSTILE, url=url)))
    monkeypatch.setattr(browser, "fetch", lambda url, **k: (CHALLENGE_200, None))
    _not_absence(oscn.search("caddo", lname="frazier", store=s, limiter=L))
    key = cache_key("oscn", "frazier caddo", county="caddo", lname="frazier",
                    fname="", year=None)
    assert s.get_entry(key) is None


# --- docs -----------------------------------------------------------------------

def test_docs_unknown_site_is_not_an_absence(kit):
    from evidence_search.sources import docs
    s, L = kit
    out = _not_absence(docs.search("pricing", site="clade-code", store=s, limiter=L))
    assert "claude-code" in out.detail, "should suggest the near match"


def test_docs_index_with_no_match_is_a_scoped_absence(kit, serve):
    from evidence_search.sources import docs
    s, L = kit
    serve(200, "# Docs\n- [Hooks](https://code.claude.com/docs/hooks)\n"
               "- [Settings](https://code.claude.com/docs/settings)\n")
    out = docs.search("zzzqqq", store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert out.not_searched and out.caveats


# --- SearXNG --------------------------------------------------------------------

def _searxng(monkeypatch, search_payload, engines=None):
    from evidence_search.engines import searxng
    engines = engines if engines is not None else [
        {"name": "google", "enabled": True, "categories": ["general"]},
        {"name": "bing", "enabled": True, "categories": ["general", "web"]},
        {"name": "arxiv", "enabled": True, "categories": ["science"]},
        {"name": "genius", "enabled": True, "categories": ["music"]},
        {"name": "yahoo", "enabled": False, "categories": ["general"]},
    ]

    def fake_fetch(url, **k):
        if url.endswith("/config"):
            return json.dumps({"engines": engines}), None
        return (search_payload if isinstance(search_payload, str)
                else json.dumps(search_payload)), None

    monkeypatch.setattr(searxng, "fetch", fake_fetch)
    return searxng


def test_searxng_counts_only_engines_in_the_requested_category(kit, monkeypatch):
    sx = _searxng(monkeypatch, {"results": [], "unresponsive_engines": []})
    s, L = kit
    out = sx.search("zzz", base="http://x", store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert out.coverage.queried == ["searxng:bing", "searxng:google"]
    assert "2 engines" in out.searched

    sci = sx.search("zzz", categories="science", base="http://x", store=s, limiter=L,
                    use_cache=False)
    assert sci.coverage.queried == ["searxng:arxiv"]


def test_searxng_cached_absence_keeps_its_caveats(kit, monkeypatch):
    sx = _searxng(monkeypatch, {"results": [], "unresponsive_engines": []})
    s, L = kit
    first = sx.search("zzz", base="http://x", store=s, limiter=L)
    again = sx.search("zzz", base="http://x", store=s, limiter=L)
    assert isinstance(first, VerifiedAbsence) and isinstance(again, VerifiedAbsence)
    assert again.coverage.cache_hits == 1
    assert again.claim() != "Not found"
    assert again.caveats == first.caveats and again.not_searched == first.not_searched
    assert [p.query for p in again.probes] == ["zzz"]


@pytest.mark.parametrize("payload", [{"error": "search failed"}, {}, [],
                                     {"results": None, "unresponsive_engines": []}])
def test_searxng_json_without_a_results_list_is_not_an_absence(kit, monkeypatch, payload):
    sx = _searxng(monkeypatch, payload)
    s, L = kit
    _not_absence(sx.search("zzz", base="http://x", store=s, limiter=L, use_cache=False))


def test_searxng_empty_later_page_says_it_is_not_page_one(kit, monkeypatch):
    sx = _searxng(monkeypatch, {"results": [], "unresponsive_engines": []})
    s, L = kit
    out = sx.search("zzz", pageno=3, base="http://x", store=s, limiter=L, use_cache=False)
    assert any("page 3" in c for c in out.caveats)


# --- news_rss -------------------------------------------------------------------

def test_news_rss_absence_states_its_scope(kit, serve):
    from evidence_search.engines import news_rss
    s, L = kit
    serve(200, '<?xml version="1.0"?><rss><channel><title>zzz</title></channel></rss>')
    out = news_rss.search("zzz", store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert out.caveats and out.not_searched and out.probes
    assert out.claim() != "Not found in 1 searched corpus/corpora"
    assert not out.is_absolute


@pytest.mark.parametrize("body", [
    '<?xml version="1.0"?><html><body><p>Before you continue to Google</p></body></html>',
    '<?xml version="1.0"?><error><message>bad request</message></error>',
    '<?xml version="1.0"?><rss></rss>',
])
def test_news_rss_wellformed_xml_that_is_not_a_feed_is_not_an_absence(kit, serve, body):
    from evidence_search.engines import news_rss
    s, L = kit
    serve(200, body)
    _not_absence(news_rss.search("zzz", store=s, limiter=L, use_cache=False))


# --- walls served with a 200 ----------------------------------------------------

INCAPSULA_STUB = (
    '<html><head><META NAME="robots" CONTENT="noindex,nofollow">'
    '<script src="/_Incapsula_Resource?SWJIYLWA=5074a744e2e3d891814e9a2dace20bd4,'
    '719d34d31c8e3a6e6fffd425f7e032f3"></script></head><body></body></html>')
AKAMAI_DENIED = (
    "<html><head><title>Access Denied</title></head><body><h1>Access Denied</h1>"
    "You don't have permission to access \"http://www.example.gov/x\" on this server."
    "<p>Reference #18.abc123.1699999999.1a2b3c</p></body></html>")


def test_incapsula_and_akamai_200_stubs_are_blockers():
    from evidence_search.core.http import detect_blocker
    assert detect_blocker(200, INCAPSULA_STUB) is Blocker.IMPERVA
    assert detect_blocker(200, AKAMAI_DENIED) is Blocker.AKAMAI
    assert detect_blocker(403, AKAMAI_DENIED) is Blocker.AKAMAI


def test_incapsula_script_on_a_served_page_is_furniture():
    """Protected sites inject the resource script into pages they do serve."""
    from evidence_search.core.http import detect_blocker
    page = ("<html><body>" + "<p>The board approved the contract on a 4-1 vote.</p>" * 40
            + '<script src="/_Incapsula_Resource?SWJIYLWA=abc"></script></body></html>')
    assert detect_blocker(200, page) is None
    article = ("<html><head><title>Access Denied</title></head><body>"
               + "<p>The agency said access was denied to reporters.</p>" * 40
               + "</body></html>")
    assert detect_blocker(200, article) is None
