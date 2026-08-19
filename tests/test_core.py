"""Tests for the guarantees that matter: typed outcomes and rate discipline."""
import tempfile, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from cascade_search.core.results import (
    Coverage, Blocker, AccessBlocker, RateLimited, VerifiedAbsence, verified_absence, Result)
from cascade_search.core.store import Store, normalize_url, cache_key
from cascade_search.core.limits import Limiter
from cascade_search.core.http import detect_blocker


def tmpstore():
    return Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")


def test_clean_coverage_yields_verified_absence():
    cov = Coverage(queried=["a", "b"], responsive=["a", "b"])
    assert isinstance(verified_absence("q", cov, "x"), VerifiedAbsence)


def test_dirty_coverage_cannot_yield_verified_absence():
    """THE core guarantee: a rate-limited engine can never produce a publishable negative."""
    for bad in (Coverage(queried=["a", "b"], responsive=["a"], rate_limited=["b"]),
                Coverage(queried=["a"], responsive=[], errored={"a": "boom"}),
                Coverage()):
        assert isinstance(verified_absence("q", bad, "x"), RateLimited)


def test_blockers_flag_browser_escalation():
    for mech in (Blocker.CLOUDFLARE, Blocker.TURNSTILE, Blocker.RECAPTCHA,
                 Blocker.DATADOME, Blocker.JS_ONLY):
        assert AccessBlocker(query="q", mechanism=mech).escalate_to_browser
    for mech in (Blocker.NOT_FOUND, Blocker.SERVER_ERROR):
        assert not AccessBlocker(query="q", mechanism=mech).escalate_to_browser


def test_url_normalization_strips_tracking():
    assert normalize_url("https://WWW.Example.com/A/?utm_source=x&id=3") == "https://example.com/A?id=3"
    assert normalize_url("http://a.com/b/") == normalize_url("http://www.a.com/b")


def test_blocker_signatures():
    assert detect_blocker(200, "<html>OSCN Turnstile</html>") is Blocker.TURNSTILE
    assert detect_blocker(200, "cf-browser-verification") is Blocker.CLOUDFLARE
    assert detect_blocker(403, "x") is Blocker.FORBIDDEN
    assert detect_blocker(200, "<html><body>" + "text " * 300 + "</body></html>") is None


def test_courtlistener_concurrent_windows():
    """5/min, 50/hr, 125/day apply concurrently; the most restrictive controls."""
    s = tmpstore(); L = Limiter(s)
    assert L.policy("courtlistener").windows == [(60, 5), (3600, 50), (86400, 125)]
    for _ in range(5):
        s.record_call("courtlistener")
    ok, retry, why = L.check("courtlistener")
    assert not ok and "5" in why


def test_oscn_session_cap():
    """OSCN Turnstile engages ~10 fetches; we cap at 8."""
    s = tmpstore(); L = Limiter(s)
    L._session_counts["oscn"] = 8
    ok, _, why = L.check("oscn")
    assert not ok and "session cap" in why


def test_metered_engines_not_cacheable():
    """Brave's standard plans forbid storing results; policy must reflect that."""
    L = Limiter(tmpstore())
    assert L.policy("brave").cacheable is False
    assert L.policy("oscn").cacheable is True


def test_unique_to_engine():
    assert Result(url="u", engines=["marginalia"]).unique_to_engine
    assert not Result(url="u", engines=["brave", "mojeek"]).unique_to_engine


def test_cache_roundtrip():
    s = tmpstore(); k = cache_key("oscn", "frazier", year=2013)
    s.put(k, "oscn", [{"url": "x"}])
    assert s.get(k) == [{"url": "x"}]
    assert s.get(cache_key("oscn", "frazier", year=2014)) is None


def test_propublica_deref():
    """SvelteKit index-referenced payloads must resolve to plain data."""
    from cascade_search.sources.propublica_disclosures import _deref
    arr = [{"result": 1, "q": 3}, [2], {"a_txt": 4}, "Blue Owl", "Jane Doe"]
    assert _deref(arr, arr[0]) == {"result": [{"a_txt": "Jane Doe"}], "q": "Blue Owl"}


def test_propublica_policy_registered():
    L = Limiter(tmpstore())
    p = L.policy("propublica_disclosures")
    assert p.min_interval_s >= 1.0 and "q=" in p.note


def test_browser_allowlist_is_public_records_only():
    """Browser escalation must never target arbitrary hosts."""
    from cascade_search.core.browser import host_allowed
    for good in ("https://www.oscn.net/dockets/x", "https://biz.sosmt.gov/y",
                 "https://storage.courtlistener.com/z"):
        assert host_allowed(good)[0], good
    for bad in ("https://example.com/x", "https://linkedin.com/in/y",
                "https://paywalled-news.com/z"):
        assert not host_allowed(bad)[0], bad


def test_gate_open_list_resume_cycle():
    """The humanomation loop: park at a gate, list it, resume with artifacts."""
    import tempfile as _tf, pathlib as _p
    from cascade_search.core import gates
    from cascade_search.core.results import GateType, AwaitingHuman, Hit

    s = tmpstore()
    g = gates.open_gate(s, source="oscn", url="https://www.oscn.net/x", query="q",
                        gate_type=GateType.CAPTCHA, capture=["page HTML"])
    assert isinstance(g, AwaitingHuman) and g.resume_token
    assert len(gates.list_gates(s)) == 1

    d = _p.Path(_tf.mkdtemp()); f = d / "solved.html"; f.write_text("<html>ok</html>")
    res, err = gates.resume(s, g.resume_token, [str(f)],
                            do_archive=False)
    assert err is None and isinstance(res, Hit)
    assert gates.list_gates(s) == []          # queue drains
    assert s.get_job(g.resume_token)["state"] == "done"


def test_gate_resume_rejects_unknown_token():
    from cascade_search.core import gates
    res, err = gates.resume(tmpstore(), "nope", [])
    assert res is None and "no job" in err


def test_identifiers_no_word_false_positives():
    """First cut matched BROADCASTING as a UEI and COURT as a CAGE code."""
    from cascade_search.core.extract import identifiers
    got = identifiers("THE BROADCASTING COURT ORDERED PAYMENT", is_html=False)
    assert "uei" not in got and "cage" not in got


def test_identifiers_match_real_investigation_values():
    from cascade_search.core.extract import identifiers
    probe = ("Award 15DDNE21P00000038, UEI VG1HDQR1Y1P5, CAGE 4ZVJ2, "
             "docket 1:16-cv-02237, CF-2013-00038, CIV-15-188-M, $250,000.00, "
             "725 ILCS 150/13.2, NAICS 611699")
    got = identifiers(probe, is_html=False)
    assert "15DDNE21P00000038" in got["federal_award"]
    assert "VG1HDQR1Y1P5" in got["uei"] and "4ZVJ2" in got["cage"]
    assert set(got["docket"]) >= {"1:16-cv-02237", "CF-2013-00038", "CIV-15-188-M"}
    assert "$250,000.00" in got["money"]


def test_grep_returns_passages_not_pages():
    from cascade_search.core.extract import grep, est_tokens
    html = "<html><body>" + ("filler " * 3000) + "NEEDLE here" + ("filler " * 3000) + "</body></html>"
    hits = grep(html, ["NEEDLE"])
    assert len(hits) == 1 and "NEEDLE" in hits[0]["context"]
    assert est_tokens(hits[0]["context"]) < est_tokens(html) / 20


def test_page_text_strips_chrome():
    from cascade_search.core.extract import page_text
    html = "<html><nav>MENU</nav><script>var x=1</script><body><p>real content</p></body></html>"
    t = page_text(html)
    assert "real content" in t and "MENU" not in t and "var x" not in t
