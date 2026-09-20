"""A negative is only as good as the record of what was asked.

Proving a negative in absolute terms is usually impossible. What IS provable is
narrower and worth stating precisely: these endpoints, asked these exact
questions, returned nothing at this time. `VerifiedAbsence` carries that record
structurally so it can be audited, re-run, and argued with.
"""
import time

from evidence_search.core.results import (Coverage, Probe, RateLimited,
                                         VerifiedAbsence, verified_absence)


def _clean():
    return Coverage(queried=["oscn"], responsive=["oscn"])


def test_absence_records_the_exact_query_per_endpoint():
    """Prose like 'searched OSCN' cannot be audited or re-run. This can."""
    v = verified_absence(
        "Quillfeather", _clean(), "oscn:caddo",
        probes=[Probe(source="oscn", endpoint="https://www.oscn.net/dockets/Results.aspx",
                      query="lname=Quillfeather", params={"db": "caddo", "year": 2013},
                      corpus="Caddo County district court docket index",
                      result_count=0, exact_match_supported=True, at=time.time())])
    p = v.probes[0]
    assert p.query == "lname=Quillfeather"
    assert p.params["db"] == "caddo"
    assert p.endpoint.startswith("https://")
    assert "0 results" in p.describe()


def test_an_absence_is_not_absolute_when_something_was_left_unsearched():
    """The honest boundary of the claim. A Caddo County silence is not an
    Oklahoma silence, and is certainly not a federal one."""
    v = verified_absence("x", _clean(), "oscn:caddo",
                         probes=[Probe(source="oscn", exact_match_supported=True)],
                         not_searched=["other Oklahoma counties", "federal courts"])
    assert v.is_absolute is False
    assert "NOT searched" in v.claim()


def test_a_fuzzy_endpoint_can_never_produce_an_absolute_negative():
    """If the endpoint did not honour the phrase, it did not ask the question."""
    v = verified_absence("x", _clean(), "web",
                         probes=[Probe(source="searxng", exact_match_supported=False)])
    assert v.is_absolute is False


def test_absolute_is_reachable_but_rare():
    """The property exists so callers stop treating a bounded negative as an
    unbounded one -- not to make the strong claim unreachable."""
    v = verified_absence("x", _clean(), "oscn:caddo",
                         probes=[Probe(source="oscn", exact_match_supported=True)])
    assert v.is_absolute is True


def test_dirty_coverage_still_refuses_regardless_of_probes():
    """The original guarantee is unchanged: a rate-limited engine cannot
    produce a publishable negative, however well-documented the probes."""
    dirty = Coverage(queried=["a", "b"], responsive=["a"], rate_limited=["b"])
    out = verified_absence("x", dirty, "s", probes=[Probe(source="a")])
    assert isinstance(out, RateLimited)
    assert not isinstance(out, VerifiedAbsence)


def test_a_probeless_absence_is_still_valid_but_visibly_weaker():
    """Callers that pass no probes get a prose-only claim. That is allowed --
    not every source can enumerate its endpoint -- but it can never read as
    absolute, so the weaker evidence produces the weaker claim."""
    v = verified_absence("x", _clean(), "some corpus")
    assert isinstance(v, VerifiedAbsence)
    assert v.is_absolute is False


# --- false absences manufactured by the tool itself ---------------------------
# Both of these shipped green on main with 209 passing tests. Neither is a
# "declared dirt" case, which is what the Coverage guard catches -- in both the
# source built a CLEAN coverage over a failure it had not noticed. That is the
# gap: the invariant is enforced on what a source REPORTS, so a source that
# fails to report is enforced on nothing.

def test_soft_block_is_not_a_document_that_was_read():
    """An AWS WAF challenge must never flow downstream as content.

    CourtListener began answering non-browser clients with HTTP 202, an empty
    body and `x-amzn-waf-action: challenge`. It names no vendor in the markup,
    so every signature pattern missed it and the empty body reached `extract`,
    which printed "the document was read, the terms are not in it" over a fetch
    that read nothing. Reported 2026-08-28 and again 2026-09-17 -- the second
    reporter could only tell because they happened to have a successful
    54,024-token fetch of the same URL earlier in scrollback.

    A negative produced this way is indistinguishable from a real zero-match,
    which makes it the exact failure this package exists to prevent.
    """
    from evidence_search.core.http import detect_blocker
    from evidence_search.core.results import AccessBlocker, Blocker

    assert detect_blocker(202, "", {"x-amzn-waf-action": "challenge"}) is Blocker.AWS_WAF
    assert detect_blocker(202, "<html><body></body></html>") is Blocker.AWS_WAF
    # A JS challenge is exactly what the browser tier exists for.
    assert AccessBlocker(query="q", mechanism=Blocker.AWS_WAF).escalate_to_browser


def test_soft_block_detection_does_not_eat_real_documents():
    """The other half: a served page must stay served.

    Size alone cannot decide this -- a legitimately short page is ordinary, and
    202 is a normal status for some APIs. Detection keys on the PAIR (challenge
    shape, no prose), so a 202 carrying real text is content.
    """
    from evidence_search.core.http import detect_blocker

    real = "<html><body>" + "opinion text " * 500 + "</body></html>"
    assert detect_blocker(200, real) is None
    assert detect_blocker(202, real) is None, "202 with prose was served, not gated"
    assert detect_blocker(200, "<html><body>Not found.</body></html>") is None
def test_unpriceable_awards_are_not_an_absence():
    """Rows we cannot price are a parse failure, not an empty corpus.

    `dollar_sum` counted only rows whose `Award Amount` was numeric, then
    reported "no priced rows" as a VerifiedAbsence at exit 11 -- which a shell
    caller reads as publishable. Feeding two real awards whose amounts arrived
    as strings ("5,000,000", a shape this module already defends against in
    _parse_counts) certified a $7.5M vendor as having no awards.

    The awards existed. Only our reading of them failed.
    """
    import evidence_search.sources.usaspending as us
    from evidence_search.core.results import AccessBlocker, VerifiedAbsence

    def _rows(path, payload):
        return {"results": [
            {"Award ID": "W1", "Recipient Name": "ACME", "Award Amount": "5,000,000"},
            {"Award ID": "W2", "Recipient Name": "ACME", "Award Amount": "2,500,000"},
        ], "page_metadata": {"hasNext": False}}, None

    orig, us._post = us._post, _rows
    try:
        out = us.dollar_sum("ACME DEFENSE LLC")
    finally:
        us._post = orig
    assert not isinstance(out, VerifiedAbsence), "awards exist; this is not an absence"
    assert isinstance(out, AccessBlocker)


def test_unread_pages_cannot_certify_absence():
    """hasNext means the API told us there was more and we did not look."""
    import evidence_search.sources.usaspending as us
    from evidence_search.core.results import VerifiedAbsence

    def _empty_more(path, payload):
        return {"results": [], "page_metadata": {"hasNext": True}}, None

    orig, us._post = us._post, _empty_more
    try:
        out = us.dollar_sum("ACME")
    finally:
        us._post = orig
    assert not isinstance(out, VerifiedAbsence)


def test_a_real_usaspending_absence_still_certifies_and_is_auditable():
    """The fixes must not make a true negative unpublishable.

    A genuinely empty result still yields VerifiedAbsence -- and now carries
    probes and the subaward caveat, so `claim()` states its own scope instead
    of the bare "Not found" it used to produce.
    """
    import evidence_search.sources.usaspending as us
    from evidence_search.core.results import VerifiedAbsence

    def _empty(path, payload):
        return {"results": [], "page_metadata": {"hasNext": False}}, None

    orig, us._post = us._post, _empty
    try:
        out = us.dollar_sum("NONEXISTENT VENDOR")
    finally:
        us._post = orig
    assert isinstance(out, VerifiedAbsence)
    assert out.probes, "a certified negative must record what was asked"
    assert "subawards" in out.not_searched
    assert out.claim() != "Not found"
