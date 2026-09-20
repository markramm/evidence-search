"""Every source entry point, swept for the one invariant, generically.

The per-source absence tests in this suite are hand-written spot checks. They
are good, and they are only as complete as the list someone thought to write --
which is how `usaspending.dollar_sum` shipped a false absence with 209 tests
green: nobody spot-checked the one function that re-implements the shell by
hand, so nothing enforced the invariant there at all.

This file is the enforcement that does not depend on remembering. It walks
EVERY registered entry point and asserts the rule the package is named for:

    a failed retrieval may never be reported as a VerifiedAbsence.

The point is the next source. A contributor who adds one and forgets to write
its absence test still gets this sweep; a contributor who adds one and builds
its own coverage by hand -- the thing that caused both live bugs -- fails here.

Adding a source means adding a row to ENTRY_POINTS. That is deliberate: it is
one line, and it is the line that makes the guarantee cover you.
"""
from __future__ import annotations

import pytest

from evidence_search.core.results import RateLimited, VerifiedAbsence


def _crossref_search(**kw):
    from evidence_search.sources import crossref
    return crossref.search("anything", **kw)


def _crossref_by_doi(**kw):
    from evidence_search.sources import crossref
    return crossref.by_doi("10.1000/xyz", **kw)


def _fedreg(**kw):
    from evidence_search.sources import federal_register
    return federal_register.search("anything", **kw)


def _courtlistener(**kw):
    from evidence_search.sources import courtlistener
    return courtlistener.search("anything", **kw)


def _propublica(**kw):
    from evidence_search.sources import propublica_disclosures
    return propublica_disclosures.search("anything", **kw)


def _oscn(**kw):
    from evidence_search.sources import oscn
    return oscn.search("caddo", lname="frazier", **kw)


def _docs(**kw):
    from evidence_search.sources import docs
    return docs.search("anything", **kw)


def _usa_search(**kw):
    from evidence_search.sources import usaspending
    return usaspending.search("anything", **kw)


def _usa_counts(**kw):
    from evidence_search.sources import usaspending
    return usaspending.counts("anything", **kw)


def _usa_dollar_sum(**kw):
    from evidence_search.sources import usaspending
    # dollar_sum has no use_cache parameter: it re-implements the shell by hand,
    # which is precisely why it carried a false absence the shell would have
    # prevented. Drop the kwarg rather than skipping the entry point.
    kw.pop("use_cache", None)
    return usaspending.dollar_sum("anything", **kw)


#: (label, callable). Every public entry point that can return an Outcome.
ENTRY_POINTS = [
    ("crossref.search", _crossref_search),
    ("crossref.by_doi", _crossref_by_doi),
    ("federal_register.search", _fedreg),
    ("courtlistener.search", _courtlistener),
    ("propublica_disclosures.search", _propublica),
    ("oscn.search", _oscn),
    ("docs.search", _docs),
    ("usaspending.search", _usa_search),
    ("usaspending.counts", _usa_counts),
    ("usaspending.dollar_sum", _usa_dollar_sum),
]


@pytest.fixture
def no_network(monkeypatch):
    """Make every outbound path fail, by whichever door the source uses."""
    def _boom(*a, **kw):
        raise AssertionError("test attempted a real network call")
    import evidence_search.core.http as http_mod
    monkeypatch.setattr(http_mod, "fetch", _boom)
    return monkeypatch


@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_transport_failure_is_never_an_absence(label, call, monkeypatch):
    """A source that could not reach its endpoint knows nothing about the world.

    This is the shape of the CourtListener soft block: the retrieval failed,
    and the question is only whether the source NOTICED. One that returns a
    verified absence here is claiming to have searched a corpus it never
    reached.
    """
    import evidence_search.core.http as http_mod
    from evidence_search.core.results import AccessBlocker, Blocker

    def _blocked(url, *, source, query="", **kw):
        return None, AccessBlocker(query=query, mechanism=Blocker.SERVER_ERROR,
                                   url=url, detail="simulated transport failure")

    monkeypatch.setattr(http_mod, "fetch", _blocked)
    # usaspending POSTs through its own helper rather than http.fetch.
    import evidence_search.sources.usaspending as us
    monkeypatch.setattr(us, "_post", lambda p, pl: (None, ("http", "simulated failure")))

    out = call(use_cache=False)
    assert not isinstance(out, VerifiedAbsence), (
        f"{label} certified an absence over a failed retrieval: {out}")


@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_rate_limited_is_never_an_absence(label, call, monkeypatch):
    """A throttled source is tooling-limited, never content-exhausted.

    The corpus logged this confusion three separate times before the tool
    existed; each cost a full dispatch cycle.
    """
    import evidence_search.core.limits as limits_mod

    class _Exhausted:
        def reserve(self, source):
            return False, 60, "simulated budget exhaustion"

        def note_outcome(self, *a, **kw):
            pass

    out = call(use_cache=False, limiter=_Exhausted())
    assert not isinstance(out, VerifiedAbsence), (
        f"{label} certified an absence while rate-limited: {out}")
    assert isinstance(out, RateLimited), (
        f"{label} should downgrade to RateLimited, got {type(out).__name__}")


#: Sources that scrape HTML rather than parse a typed payload. A JSON source
#: can tell a malformed body from an empty one because the shape is declared;
#: a scraper counting rows cannot. Listed explicitly so the exemption is
#: visible and shrinks as sources gain positive shape checks -- never so the
#: sweep quietly passes.
_HTML_SCRAPERS = {"oscn.search", "docs.search"}


@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_malformed_success_payload_is_never_an_absence(label, call, monkeypatch):
    """A 200 whose body we cannot read is a parse failure, not an empty corpus.

    This is the `dollar_sum` bug generalised: the endpoint answered, the rows
    were there, and our reading of them failed. Reporting that as absence
    publishes a negative about a corpus that in fact had content.

    A source is allowed to return AccessBlocker, RateLimited, or even a Hit
    here. The single forbidden answer is "it isn't there."
    """
    import evidence_search.core.http as http_mod

    def _garbage(url, *, source, query="", **kw):
        return "<!doctype html><html><body>\x00\x01 not the expected shape", None

    monkeypatch.setattr(http_mod, "fetch", _garbage)
    import evidence_search.sources.usaspending as us
    # Award rows present, amounts unreadable -- the exact live-bug shape.
    monkeypatch.setattr(us, "_post", lambda p, pl: (
        {"results": [{"Award ID": "W1", "Recipient Name": "ACME",
                      "Award Amount": "5,000,000"}],
         "page_metadata": {"hasNext": False}}, None))

    try:
        out = call(use_cache=False)
    except (ValueError, KeyError, TypeError, AttributeError):
        # Raising is an acceptable answer -- it cannot be mistaken for a finding.
        # run_source converts a ValueError from parse() into an AccessBlocker
        # ("payload shape changed"), which is the designed route.
        return
    if label in _HTML_SCRAPERS and isinstance(out, VerifiedAbsence):
        pytest.xfail(
            f"{label} reads an unrecognised page as zero rows. An HTML scraper "
            "cannot tell 'the page served no results' from 'the page is not the "
            "page I know' by row count alone -- it needs a positive shape check "
            "(the results table exists) the way oscn already has a positive "
            "sentinel for its real empty page. Tracked, not fixed here.")
    assert not isinstance(out, VerifiedAbsence), (
        f"{label} certified an absence over an unreadable payload: {out}")
