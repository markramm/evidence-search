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
    """OSCN Turnstile engages ~10 fetches; we cap at 8.

    The cap must live in the SHARED ledger -- see test_concurrency.py for the
    cross-process guarantee. Here we assert it fires at all.
    """
    s = tmpstore(); L = Limiter(s)
    for _ in range(8):
        s.record_call("oscn")
    ok, _, why = L.check("oscn")
    assert not ok and "session cap" in why


def test_cacheability_is_a_policy_field():
    """Where an engine's terms forbid storing results, policy must say so.

    The brave/marginalia policies this once asserted were removed: no code path
    ever reserved them, so they were dead config that a live worker mistook for
    a real budget. `cacheable` remains a policy field for any future metered
    source whose terms forbid storage.
    """
    L = Limiter(tmpstore())
    assert L.policy("oscn").cacheable is True
    assert L.policy("searxng").cacheable is True


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


def test_normalize_url_keeps_semantic_gov_params():
    """`ref` and `source` are frequently SEMANTIC on government sites.

    Stripping them as tracking collapsed two distinct records into one in the
    dedup path.
    """
    a = normalize_url("https://gov.example/case?source=oscn&id=1")
    b = normalize_url("https://gov.example/case?source=recap&id=1")
    assert a != b, "distinct source= records must not collapse"
    assert "ref=" in normalize_url("https://gov.example/x?ref=dataset-a")


def test_normalize_url_still_strips_analytics():
    assert normalize_url("https://WWW.Example.com/A/?utm_source=x&id=3") == "https://example.com/A?id=3"
    for junk in ("fbclid=1", "gclid=1", "_ga=1", "mc_cid=1", "igshid=1"):
        assert junk.split("=")[0] not in normalize_url(f"https://a.com/b?{junk}&keep=1")
        assert "keep=1" in normalize_url(f"https://a.com/b?{junk}&keep=1")


def test_js_only_requires_spa_evidence_not_just_smallness():
    """JS_ONLY escalates to a browser, so a false positive costs a Playwright launch."""
    shell = '<html><head><script src="/main.a1b2.js"></script>' \
            '<script src="/runtime.js"></script></head><body><div id="root"></div></body></html>'
    assert detect_blocker(200, shell) is Blocker.JS_ONLY

    # Small, script-bearing, but NOT a shell: no mount point, no bundle.
    stub = '<html><head><script>var a=1</script><script>var b=2</script></head>' \
           '<body><p>Record not available.</p></body></html>'
    assert detect_blocker(200, stub) is None


def test_unreadable_pdf_is_a_blocker_not_binary_noise(tmp_path, capsys):
    """The token lever must not run backwards.

    `extract --text` on a 2.4MB PDF once reported "-242.3% reduction" and would
    have pushed ~2M tokens of binary noise into the caller's context -- the exact
    opposite of what the command exists to do. Found by a worker on a real task.

    A PDF with no text layer and OCR disabled is now a named blocker.
    """
    from cascade_search.cli import main
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.7\n" + b"\x00\x01\x02binary noise" * 500)
    code = main(["extract", str(pdf), "--text", "--no-ocr"])
    out = capsys.readouterr().out
    assert code == 2, "an unreadable PDF must be a blocker, not silently mangled"
    assert "Scanned PDF" in out or "text layer" in out
    assert "% reduction" not in out, "must not report a savings figure for a refusal"


def test_savings_reports_expansion_rather_than_negative_reduction():
    """A negative 'reduction' is not a saving and must not print as one.

    It happens legitimately -- OCR output plus its provenance block can exceed a
    short scanned page's decoded text -- but "-285.3% reduction" reads like a
    malfunction rather than the honest 'this source was already small'.
    """
    from cascade_search.core.extract import savings
    s = savings("short source", "a much longer extracted payload " * 20)
    assert s["expanded"] is True
    assert savings("x" * 4000, "y" * 100)["expanded"] is False


def test_courtlistener_keeps_the_fields_an_investigator_needs():
    """The old allowlist kept 4 of 30 fields and was written against type=o.

    When type=r was added nobody rechecked the shape, so RECAP rows lost `firm`
    and `attorney` (who retained the expert -- the money question), `cause`,
    `party`, and `recap_documents` (which say whether a filing is an expert
    disclosure). A worker counting an industry fell back to raw HTTP for all of
    it. It also emitted an empty url while holding the docket_id needed to build
    one.
    """
    import json
    from cascade_search.sources.courtlistener import _parse
    body = json.dumps({"count": 85, "next": "?page=2", "results": [{
        "caseName": "Dyer v. City of Mesquite Texas",
        "docketNumber": "3:15-cv-02638", "docket_id": 5409345,
        "docket_absolute_url": "/docket/5409345/dyer/", "absolute_url": None,
        "court": "N.D. Tex.", "cause": "42:1983 Civil Rights Act",
        "firm": ["Stoy Law Group PLLC"], "attorney": ["Christopher Edward Stoy"],
        "recap_documents": [{"description": "Designation of Experts",
                             "absolute_url": "/docket/5409345/100/5/"}],
    }]})
    r = _parse(body)[0]
    assert r.url.endswith("/docket/5409345/dyer/"), "must build a usable URL"
    assert r.meta["total_matches"] == 85
    assert r.meta["cause"] == "42:1983 Civil Rights Act"
    assert r.meta["firm"] == ["Stoy Law Group PLLC"]
    assert r.meta["attorney"] == ["Christopher Edward Stoy"]
    assert r.meta["recap_documents"][0]["description"] == "Designation of Experts"


def test_courtlistener_falls_back_to_docket_id_for_the_url():
    import json
    from cascade_search.sources.courtlistener import _parse
    body = json.dumps({"count": 1, "results": [
        {"caseName": "X v. Y", "docket_id": 42, "absolute_url": None}]})
    assert _parse(body)[0].url.endswith("/docket/42/")


def test_full_records_are_stored_while_results_stay_curated():
    """Store everything; return what is generally useful; fetch the rest by id.

    Two failure modes bracket this. Returning 4 of 30 fields lost `firm` and
    `attorney` -- who retained the expert, which was the whole question a worker
    was answering. Returning all 30 inline spends the caller's context on fields
    nobody asked for, which is what `extract` exists to prevent.
    """
    import json
    import pathlib
    import tempfile
    from cascade_search.core.store import Store
    from cascade_search.sources.courtlistener import _parse

    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    body = json.dumps({"count": 85, "results": [{
        "caseName": "Dyer v. City of Mesquite Texas", "docket_id": 5409345,
        "docketNumber": "3:15-cv-02638", "court": "N.D. Tex.",
        "cause": "42:1983 Civil Rights Act", "firm": ["Stoy Law Group PLLC"],
        "attorney": ["Christopher Edward Stoy"],
        "pacer_case_id": 263338, "assigned_to_id": 352,
        "recap_documents": [{"description": "Designation of Experts"}],
    }]})
    r = _parse(body, query="probe", store=s)[0]

    # curated: the fields that answer questions on this beat
    assert r.meta["cause"] == "42:1983 Civil Rights Act"
    assert r.meta["firm"] == ["Stoy Law Group PLLC"]
    assert r.meta["record_id"] == "courtlistener:5409345"
    assert "pacer_case_id" not in r.meta, "internal ids should not ride along inline"

    # full: everything the source sent, retrievable by id
    rec = s.get_record("courtlistener:5409345")
    assert rec is not None
    assert rec["payload"]["pacer_case_id"] == 263338
    # The record holds fields the curated view deliberately omits. Assert that
    # directly rather than comparing lengths -- the earlier arithmetic version
    # broke the moment paging metadata was added to meta, which told us nothing.
    omitted = set(rec["payload"]) - set(r.meta)
    assert "pacer_case_id" in omitted and "assigned_to_id" in omitted


def test_a_storage_failure_never_loses_the_caller_results():
    """Persisting records is a convenience; returning results is the job."""
    import json
    from cascade_search.sources.courtlistener import _parse

    class Broken:
        def put_record(self, *a, **k):
            raise RuntimeError("disk on fire")

    body = json.dumps({"count": 1, "results": [
        {"caseName": "X v. Y", "docket_id": 1, "cause": "42:1983"}]})
    out = _parse(body, query="q", store=Broken())
    assert len(out) == 1 and out[0].meta["cause"] == "42:1983"


def test_oscn_rejects_an_unknown_county_instead_of_searching_it():
    """OSCN answers a bad db= with a page that parses as "no records".

    So a typo would read as a VERIFIED ABSENCE on a records question -- a wrong
    negative, which is the failure this package exists to prevent. A worker had
    to guess `oklahoma` from the `caddo` example with no way to check.
    """
    from cascade_search.core.results import AccessBlocker
    from cascade_search.sources import oscn
    out = oscn.search("oklohoma", lname="Smith")
    assert isinstance(out, AccessBlocker)
    assert "oklahoma" in out.detail, "should suggest the near match"
    assert "absence" in out.detail


def test_oscn_county_list_is_complete_and_normalises():
    from cascade_search.sources import oscn
    assert len(oscn.COUNTIES) == 77, "Oklahoma has 77 counties"
    for c in ("caddo", "oklahoma", "tulsa", "rogermills", "mcclain"):
        assert c in oscn.COUNTIES
    assert {"oksc", "okca", "okcr"} <= oscn.APPELLATE


def test_gate_creation_dedupes_on_url(tmp_path):
    """One search parked three times under three tokens meant a person would
    have solved the identical Turnstile three times. Nine open gates covered
    five distinct URLs."""
    from cascade_search.core.store import Store
    s = Store(tmp_path / "t.db")
    p = {"url": "https://www.oscn.net/dockets/Results.aspx?db=tulsa", "query": "x"}
    t1 = s.create_job("oscn", "awaiting_human", p)
    t2 = s.create_job("oscn", "awaiting_human", dict(p))
    t3 = s.create_job("oscn", "awaiting_human", {"url": "https://other", "query": "y"})
    assert t1 == t2, "same target must reuse the original token"
    assert t1 != t3
    assert len(s.list_jobs("awaiting_human")) == 2


def test_extract_announces_display_truncation(tmp_path, capsys):
    """Silent truncation corrupted seven files for a worker who only noticed
    when parsing failed -- data loss that looks like success."""
    from cascade_search.cli import main
    f = tmp_path / "big.txt"
    f.write_text("x" * 9000)
    main(["extract", str(f), "--text"])
    out = capsys.readouterr().out
    assert "TRUNCATED for display" in out
    assert "9,000 chars total" in out
    assert "--json" in out, "must name the way to get the full text"


def test_global_flags_are_accepted_after_the_subcommand():
    """argparse's bare 'unrecognized arguments: --json' gave no hint which way
    to move the flag. A worker lost time to it; accept either order."""
    from cascade_search.cli import main
    assert main(["extract", "/etc/hosts", "--text", "--json"]) == 0
    assert main(["--json", "extract", "/etc/hosts", "--text"]) == 0


def test_grep_cap_is_per_pattern_and_never_skips_a_pattern():
    """The cap used to be global with an early return.

    A first pattern with 40 matches meant later patterns were NEVER SEARCHED,
    and the result read as "zero matches, 100.0% reduction, exit 0" -- a false
    absence produced by the search path itself. Found by a worker whose terms
    were provably present in the document.
    """
    from cascade_search.core.extract import grep
    text = ("COMMON " * 200) + " RARETERM " + ("filler " * 50)
    hits = grep(text, ["COMMON", "RARETERM"], is_html=False)
    pats = {h["pattern"] for h in hits}
    assert "RARETERM" in pats, "a later pattern must still be searched"
    assert "COMMON" in pats


def test_grep_reports_its_true_match_count_when_capped():
    """A capped result must not read as a complete one."""
    from cascade_search.core.extract import grep
    hits = grep("HIT " * 500, ["HIT"], is_html=False, max_hits=10)
    real = [h for h in hits if h["pattern"] == "HIT"]
    note = [h for h in hits if h["pattern"] == "__truncated__"]
    assert len(real) == 10
    assert note and "500 times" in note[0]["context"]


def test_grep_does_not_flag_truncation_when_under_the_cap():
    from cascade_search.core.extract import grep
    hits = grep("one HIT here", ["HIT"], is_html=False)
    assert not any(h["pattern"] == "__truncated__" for h in hits)


def test_a_blocked_host_with_a_known_json_route_says_so():
    """A worker found loc.gov's ?fo=json endpoint by hand after the HTML 403'd.

    Good instinct, wasted pass. The blocker now names the route -- but only for
    hosts where it was VERIFIED, and only as a hint, because it does not
    generalise: loc.gov /item/ JSON works while /collections/ JSON is blocked
    too. An automatic rewrite would have been wrong.
    """
    import cascade_search.core.http as http
    assert "www.loc.gov" in http._JSON_ESCAPE_HATCH
    hint = http._JSON_ESCAPE_HATCH["www.loc.gov"]
    assert "fo=json" in hint
    assert "collections" in hint, "must state the limit of the workaround"


def test_grep_with_zero_matches_does_not_read_as_success(tmp_path, capsys):
    """"100.0% reduction" over zero matches reads as success and means the
    opposite. Reported twice by workers; it nearly produced a wrong conclusion
    on an attorney-of-record question."""
    from cascade_search.cli import main
    f = tmp_path / "doc.txt"
    f.write_text("some text without the term")
    main(["extract", str(f), "--grep", "ZZZNOTPRESENT"])
    out = capsys.readouterr().out
    assert "NO MATCHES" in out
    assert "IN THIS DOCUMENT ONLY" in out, "must bound the absence to the document"
