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
