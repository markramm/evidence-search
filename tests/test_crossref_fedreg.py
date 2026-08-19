"""Crossref and Federal Register: two sources, opposite count semantics.

The distinction is the point. Crossref matches loosely across ~150M records, so
its totals measure nothing; Federal Register searches a defined corpus, so its
count is real. Treating them the same would produce exactly the confident-wrong-
number failure this package keeps hitting.
"""
import json
import pathlib
import tempfile

from cascade_search.core.limits import Limiter
from cascade_search.core.results import (AccessBlocker, Hit, RateLimited,
                                         VerifiedAbsence)
from cascade_search.core.store import Store
from cascade_search.sources import crossref as cr
from cascade_search.sources import federal_register as fr


def _kit():
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    return s, Limiter(s)


def test_doi_is_recognised_and_routed_to_exact_lookup():
    assert cr.looks_like_doi("10.1177/10986111251357498")
    assert not cr.looks_like_doi("Forced Science")


def test_doi_result_is_marked_exact_and_carries_no_verify_warning(monkeypatch):
    """DOI lookup is authoritative; a title search is not. The flag is the
    difference between a citation and a lead."""
    monkeypatch.setattr(cr, "fetch", lambda *a, **k: (json.dumps({"message": {
        "DOI": "10.1177/10986111251357498",
        "title": ["Forced Science: A Critical Appraisal"],
        "container-title": ["Police Quarterly"],
        "author": [{"given": "Ian T.", "family": "Adams"},
                   {"given": "Seth", "family": "Stoughton"}],
        "issued": {"date-parts": [[2025]]}}}), None))
    s, L = _kit()
    out = cr.by_doi("10.1177/10986111251357498", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    m = out.results[0].meta
    assert m["exact"] is True
    assert m["verify_note"] is None
    assert m["journal"] == "Police Quarterly"
    assert "Seth Stoughton" in m["authors"]


def test_title_search_warns_that_it_is_fuzzy(monkeypatch):
    """A live probe returned 'Forced to Pursue Science: Entity List Triggers'
    as the top hit for 'Forced Science' -- a plausible near-miss is the failure
    mode here, so the warning rides on every non-exact result."""
    monkeypatch.setattr(cr, "fetch", lambda *a, **k: (json.dumps({"message": {
        "total-results": 678393,
        "items": [{"DOI": "10.x/y", "title": ["Forced to Pursue Science"]}]}}), None))
    s, L = _kit()
    out = cr.search("Forced Science", store=s, limiter=L, use_cache=False)
    m = out.results[0].meta
    assert m["exact"] is False
    assert "fuzzy" in m["verify_note"].lower()


def test_unregistered_doi_is_an_absence_not_a_block(monkeypatch):
    """A 404 on a DOI lookup means the DOI is not registered -- a finding."""
    from cascade_search.core.results import AccessBlocker as AB, Blocker
    monkeypatch.setattr(cr, "fetch", lambda *a, **k: (
        None, AB(query="x", mechanism=Blocker.NOT_FOUND, url="u")))
    s, L = _kit()
    out = cr.by_doi("10.9999/nope", store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert "not registered" in out.searched


def test_fedreg_count_is_a_real_total(monkeypatch):
    """Unlike Crossref, this corpus is defined, so the count is a measurement."""
    monkeypatch.setattr(fr, "fetch", lambda *a, **k: (json.dumps({
        "count": 729, "results": [{
            "document_number": "2026-15726", "title": "Visas: Visa Bond Program",
            "type": "Rule", "publication_date": "2026-08-03",
            "html_url": "https://www.federalregister.gov/documents/x",
            "agencies": [{"name": "State Department"}]}]}), None))
    s, L = _kit()
    out = fr.search("immigration detention", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert out.results[0].meta["total_matches"] == 729
    assert out.results[0].meta["document_number"] == "2026-15726"


def test_fedreg_404_is_absence_but_503_is_a_blocker(monkeypatch):
    """Observed live: a transient 503 mid-session. Conflating it with 'no
    matching documents' is precisely the error this package exists to prevent."""
    from cascade_search.core.results import AccessBlocker as AB, Blocker
    monkeypatch.setattr(fr, "fetch", lambda *a, **k: (
        None, AB(query="x", mechanism=Blocker.NOT_FOUND, url="u")))
    s, L = _kit()
    assert isinstance(fr.search("nothing", store=s, limiter=L, use_cache=False),
                      VerifiedAbsence)

    monkeypatch.setattr(fr, "fetch", lambda *a, **k: (
        None, AB(query="x", mechanism=Blocker.SERVER_ERROR, url="u", detail="HTTP 503")))
    s2, L2 = _kit()
    out = fr.search("immigration", store=s2, limiter=L2, use_cache=False)
    assert isinstance(out, AccessBlocker) and not isinstance(out, VerifiedAbsence)
