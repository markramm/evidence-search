"""USAspending: the countable primitive.

Two workers independently called this "the load-bearing source here" on a
procurement beat and fell back to raw HTTP in its absence.
"""
import pathlib
import tempfile

from evidence_search.core.limits import Limiter
from evidence_search.core.results import Hit, RateLimited, VerifiedAbsence
from evidence_search.core.store import Store
from evidence_search.sources import usaspending as usa


def _kit():
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    return s, Limiter(s)


def test_internal_id_is_not_requestable():
    """Asking for `internal_id` 500s the endpoint; the API returns it anyway.

    Isolated field-by-field against the live API -- a full field list failed
    while each field alone succeeded except this one.
    """
    assert "internal_id" not in usa.FIELDS
    assert "Award ID" in usa.FIELDS


def test_counts_returns_a_real_total(monkeypatch):
    """This is why the source exists. `web` cannot count; this can."""
    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (
        {"results": {"contracts": 15, "idvs": 2, "grants": 0, "loans": 0}}, None))
    s, L = _kit()
    out = usa.counts("Relentless LLC", store=s, limiter=L)
    assert isinstance(out, Hit)
    assert out.results[0].meta["total_awards"] == 17
    assert out.results[0].meta["by_type"]["contracts"] == 15


def test_keyword_counts_carry_a_caveat(monkeypatch):
    """A keyword count counts records CONTAINING the words, not awards TO a vendor.

    Searching "Force Science" by keyword returns NURAD Technologies, because the
    words appear somewhere in the record. Reporting that as a vendor total would
    be the confident-wrong-number failure this package exists to prevent.
    """
    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (
        {"results": {"contracts": 9}}, None))
    s, L = _kit()
    by_name = usa.counts("x", by_recipient=True, store=s, limiter=L)
    assert by_name.results[0].meta["count_caveat"] is None

    s2, L2 = _kit()
    by_kw = usa.counts("x", by_recipient=False, store=s2, limiter=L2)
    assert "KEYWORD" in by_kw.results[0].meta["count_caveat"]
    assert "FUZZY" in by_kw.results[0].meta["search_mode"]


def test_zero_awards_is_a_verified_absence(monkeypatch):
    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (
        {"results": {"contracts": 0, "grants": 0}}, None))
    s, L = _kit()
    out = usa.counts("Nonexistent Vendor LLC", store=s, limiter=L)
    assert isinstance(out, VerifiedAbsence)
    assert out.coverage.is_clean


def test_a_server_error_is_never_an_absence(monkeypatch):
    """The 500 found in testing must not read as 'this vendor has no awards'."""
    from evidence_search.core.results import AccessBlocker
    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (
        None, ("status", "HTTP 500: Server Error")))
    s, L = _kit()
    out = usa.counts("x", store=s, limiter=L)
    assert isinstance(out, AccessBlocker)
    assert not isinstance(out, VerifiedAbsence)


def test_timeouts_are_rate_limited_not_blocked(monkeypatch):
    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (None, ("timeout", "slow")))
    s, L = _kit()
    assert isinstance(usa.counts("x", store=s, limiter=L), RateLimited)


def test_award_groups_are_never_mixed_on_a_listing():
    """USAspending rejects a mixed award_type_codes list with HTTP 422.

    The COUNT endpoint is laxer and accepts one, which is how a combined
    ALL_AWARD_TYPES shipped and then failed only on the LISTING path: --count
    worked, --limit 422'd. Groups are read from the API's own error response --
    a first correction guessed three groups and was still wrong, because loans,
    grants, direct payments and other-financial-assistance are four.
    """
    assert len(usa.AWARD_GROUPS) == 6
    seen = set()
    for codes in usa.AWARD_GROUPS.values():
        assert not (seen & set(codes)), "groups must not overlap"
        seen |= set(codes)
    assert set(usa.ALL_AWARD_TYPES) == seen


def test_mixed_groups_are_refused_before_the_api_422s(monkeypatch):
    from evidence_search.core.results import AccessBlocker
    s, L = _kit()
    out = usa.search("x", award_types=usa.CONTRACT_TYPES + usa.LOAN_TYPES,
                     store=s, limiter=L, use_cache=False)
    assert isinstance(out, AccessBlocker)
    assert "one group at a time" in out.detail


def test_sort_is_group_aware(monkeypatch):
    """Loans have no 'Award Amount' field; sorting on it returns HTTP 400
    ('not found in Loan Award mappings')."""
    seen = {}

    def spy(path, body, timeout=45.0):
        seen["sort"] = body.get("sort")
        return {"results": [], "page_metadata": {}}, None

    monkeypatch.setattr(usa, "_post", spy)
    # A fresh store per call: sharing one means the min-interval refuses the
    # second reservation, _post never runs, and the assertion reads a stale
    # value from the first call rather than testing anything.
    s1, L1 = _kit()
    usa.search("x", award_types=usa.CONTRACT_TYPES, store=s1, limiter=L1, use_cache=False)
    assert seen["sort"] == "Award Amount"

    s2, L2 = _kit()
    usa.search("x", award_types=usa.LOAN_TYPES, store=s2, limiter=L2, use_cache=False)
    assert seen["sort"] == "Award ID"


def test_dollar_sum_says_whether_it_is_complete_or_a_floor(monkeypatch):
    """Workers hand-scraped every dollar total before --sum existed, and a
    page-capped scrape is a FLOOR. Reporting a floor as a total is the
    confident-wrong-number failure this package keeps hitting."""
    pages = [
        ({"results": [{"Award Amount": 100.0}, {"Award Amount": 50.0}],
          "page_metadata": {"hasNext": True}}, None),
        ({"results": [{"Award Amount": 25.0}], "page_metadata": {"hasNext": False}}, None),
    ]
    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: pages.pop(0))
    s, L = _kit()
    out = usa.dollar_sum("x", store=s, limiter=L, max_pages=5)
    assert out.results[0].meta["dollar_total"] == 175.0
    assert out.results[0].meta["complete"] is True
    assert out.results[0].meta["caveat"] is None


def test_dollar_sum_flags_a_page_cap_as_a_floor(monkeypatch):
    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (
        {"results": [{"Award Amount": 10.0}], "page_metadata": {"hasNext": True}}, None))
    s, L = _kit()
    out = usa.dollar_sum("x", store=s, limiter=L, max_pages=2)
    m = out.results[0].meta
    assert m["complete"] is False
    assert "FLOOR" in out.results[0].title or "AT LEAST" in out.results[0].title
    assert m["caveat"]


def test_detail_surfaces_psc_and_naics(monkeypatch):
    """THREE workers independently reimplemented this against the raw API.

    Neither --group nor `record` surfaces PSC/NAICS -- both return the search
    summary, and the codes live only on the award-detail endpoint. Those codes
    are the good evidence on this beat because they are the BUYER's
    classification, not the vendor's marketing.
    """
    import httpx
    class R:
        status_code = 200
        def json(self):
            return {"piid": "140P2120C0021",
                    "description": "LAW ENFORCEMENT DE-ESCALATION TRAINING DEVELOPMENT",
                    "recipient": {"recipient_name": "LIFELINE TRAINING, LTD"},
                    "latest_transaction_contract_data": {
                        "product_or_service_code": "U008",
                        "product_or_service_description": "EDUCATION/TRAINING-CURRICULUM",
                        "naics": "611430"}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    s, L = _kit()
    out = usa.detail("usaspending:277838719", store=s, limiter=L)
    m = out.results[0].meta
    assert m["psc"] == "U008"
    assert m["naics"] == "611430"
    assert "DE-ESCALATION" in m["description"]


def test_detail_names_absent_codes_instead_of_dropping_the_line(monkeypatch):
    """Silence is ambiguous where the codes ARE the evidence.

    A worker comparing two awards got a PSC line on one and nothing on the
    other, and could not tell whether the award has no PSC or whether --detail
    failed to print it. One is a finding, the other is a bug. They nearly wrote
    "no PSC assigned" on the strength of a blank. Award 307695512 really does
    have no PSC -- so say that, rather than leaving a gap to be interpreted.
    """
    import httpx
    class R:
        status_code = 200
        def json(self):
            return {"piid": "HSCGG710PPAR228",
                    "recipient": {"recipient_name": "BOBIT BUSINESS MEDIA INC."},
                    "latest_transaction_contract_data": {
                        "naics": "561920",
                        "naics_description": "CONVENTION AND TRADE SHOW ORGANIZERS"}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    s, L = _kit()
    snip = usa.detail("307695512", store=s, limiter=L).results[0].snippet
    assert "PSC: (none in record)" in snip
    assert "561920" in snip


def test_detail_renders_the_awards_own_words(monkeypatch):
    """`description` was in meta but never printed, so it read as missing.

    It is where "INVESTIGATIVE CASE MANAGEMENT (ICM) OPERATIONS AND
    MAINTENANCE" actually lives -- the field that says what a contract IS. A
    worker dropped to raw curl for it during the most load-bearing check of
    their task.
    """
    import httpx
    class R:
        status_code = 200
        def json(self):
            return {"piid": "X", "recipient": {"recipient_name": "N"},
                    "description": "INVESTIGATIVE CASE MANAGEMENT O&M",
                    "latest_transaction_contract_data": {}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    s, L = _kit()
    snip = usa.detail("1", store=s, limiter=L).results[0].snippet
    assert "INVESTIGATIVE CASE MANAGEMENT" in snip
    assert "PSC: (none in record)" in snip and "NAICS: (none in record)" in snip


def test_detail_accepts_a_prefixed_or_bare_id(monkeypatch):
    """A constructed CONT_AWD_... string 404s; the numeric id is what works."""
    import httpx
    seen = {}
    class R:
        status_code = 200
        def json(self): return {"recipient": {}, "latest_transaction_contract_data": {}}
    def spy(url, **kw):
        seen["url"] = url
        return R()
    monkeypatch.setattr(httpx, "get", spy)
    s, L = _kit()
    usa.detail("usaspending:12345", store=s, limiter=L)
    assert seen["url"].endswith("/awards/12345/")


def test_detail_ignores_the_positional_query_and_says_so(monkeypatch, capsys):
    """--detail is keyed on the AWARD ID; the positional query is meaningless.

    Silently ignoring it meant a worker who mistyped a vendor name got ANOTHER
    VENDOR'S record with no warning -- on this beat, attributing one company's
    contract to another. Found by a worker within an hour of the command
    shipping.
    """
    import httpx
    from evidence_search.cli import main
    class R:
        status_code = 200
        def json(self):
            return {"piid": "140P2120C0021",
                    "recipient": {"recipient_name": "LIFELINE TRAINING, LTD"},
                    "latest_transaction_contract_data": {"product_or_service_code": "U008"}}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    main(["usaspending", "TOTALLY WRONG VENDOR", "--detail", "277838719"])
    err = capsys.readouterr().err
    assert "was NOT used to select this record" in err
    assert "LIFELINE TRAINING" in err


def test_detail_works_without_a_query():
    """The query became optional so the correct usage is also the simple one."""
    from evidence_search.cli import main
    import inspect
    src = inspect.getsource(main)
    assert 'nargs="?"' in src


def test_dollar_sum_honours_date_bounds(monkeypatch):
    """--sum took NO date parameters while the CLI accepted --from/--to.

    A bounded query silently returned the ALL-TIME total and labelled it
    "complete" -- verified live as a 26x overstatement ($3,298,091.75 all-time
    vs $127,494.00 from 2026-01-01). A worker caught it by cross-checking
    against --count, which respected the same bounds. A wrong total that says
    complete is worse than no total.
    """
    seen = {}

    def spy(path, body, timeout=45.0):
        seen["filters"] = body.get("filters", {})
        return {"results": [], "page_metadata": {"hasNext": False}}, None

    monkeypatch.setattr(usa, "_post", spy)
    s, L = _kit()
    usa.dollar_sum("x", date_from="2026-01-01", date_to="2026-06-30",
                   store=s, limiter=L)
    tp = seen["filters"].get("time_period")
    assert tp, "date bounds must reach the API"
    assert tp[0]["start_date"] == "2026-01-01"
    assert tp[0]["end_date"] == "2026-06-30"


def test_dollar_sum_reports_the_scope_it_summed(monkeypatch):
    """A total is uninterpretable without knowing what window it covers."""
    monkeypatch.setattr(usa, "_post", lambda p, b, timeout=45.0: (
        {"results": [{"Award Amount": 10.0}], "page_metadata": {"hasNext": False}}, None))
    s, L = _kit()
    out = usa.dollar_sum("x", store=s, limiter=L)
    assert out.results[0].meta["scope"] == "all time"

    s2, L2 = _kit()
    out2 = usa.dollar_sum("x", date_from="2026-01-01", store=s2, limiter=L2)
    assert "2026-01-01" in out2.results[0].meta["scope"]


def test_sum_warns_when_the_query_name_is_not_the_recipient_name():
    """recipient_search_text is a SEARCH, not an entity resolver.

    Querying "Constellis" returns records for TRIPLE CANOPY INC -- verified live.
    Nothing in the output said so, and summing that against a "Triple Canopy"
    query double-counted the same contracts into $10.73B, the exact wrong figure
    found sitting in a draft with an editor.
    """
    c = usa._entity_caveat("Constellis", ["TRIPLE CANOPY INC"], 1)
    assert c and "not the name on these awards" in c
    assert usa._name_matches("Constellis", ["TRIPLE CANOPY INC"]) is False


def test_sum_warns_when_several_recipient_names_matched():
    c = usa._entity_caveat("Acme", ["ACME INC", "ACME HOLDINGS"], 2)
    assert c and "DISTINCT RECIPIENT NAMES" in c
    assert "double-count" in c


def test_sum_is_quiet_when_the_entity_resolved_cleanly():
    """Do not cry wolf -- a clean single match must produce no caveat."""
    assert usa._entity_caveat("Lifeline Training", ["LIFELINE TRAINING, LTD"], 1) is None
    assert usa._name_matches("Lifeline Training", ["LIFELINE TRAINING, LTD"]) is True


def _force_404(monkeypatch):
    import httpx
    class _R:
        status_code = 404
        def json(self): return {}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _R())


def test_piid_to_detail_is_a_blocker_not_an_absence(monkeypatch):
    """A PIID passed to --detail must never read as a publishable negative.

    /awards/<id>/ keys on USAspending's INTERNAL numeric id. A PIID like
    70CDCR26FR0000001 -- the identifier a researcher actually has in hand --
    404s there even though the award exists and a recipient search returns it.
    Rendering that 404 as VerifiedAbsence tells the researcher the record is not
    there, which is the worst thing this tool can say.
    """
    from evidence_search.core.results import AccessBlocker, Blocker
    _force_404(monkeypatch)
    store, limiter = _kit()

    out = usa.detail("70CDCR26FR0000001", store, limiter)
    assert isinstance(out, AccessBlocker), type(out)
    assert out.mechanism is Blocker.WRONG_ID_TYPE
    assert "NOT AN ABSENCE" in out.detail


def test_numeric_id_that_404s_is_a_real_absence(monkeypatch):
    """The narrow fix must not blunt the genuine negative it sits next to."""
    _force_404(monkeypatch)
    store, limiter = _kit()

    out = usa.detail("999999999999", store, limiter)
    assert isinstance(out, VerifiedAbsence), type(out)
    assert "numeric award id" in out.searched
