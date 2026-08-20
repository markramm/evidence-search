"""Crossref and Federal Register: two sources, opposite count semantics.

The distinction is the point. Crossref matches loosely across ~150M records, so
its totals measure nothing; Federal Register searches a defined corpus, so its
count is real. Treating them the same would produce exactly the confident-wrong-
number failure this package keeps hitting.
"""
import json
import pathlib
import tempfile

from evidence_search.core import http as core_http
from evidence_search.core.limits import Limiter
from evidence_search.core.results import (AccessBlocker, Hit, RateLimited,
                                         VerifiedAbsence)
from evidence_search.core.store import Store
from evidence_search.sources import crossref as cr
from evidence_search.sources import federal_register as fr


def _kit():
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    return s, Limiter(s)


def test_doi_is_recognised_and_routed_to_exact_lookup():
    assert cr.looks_like_doi("10.1177/10986111251357498")
    assert not cr.looks_like_doi("Forced Science")


def test_doi_result_is_marked_exact_and_carries_no_verify_warning(monkeypatch):
    """DOI lookup is authoritative; a title search is not. The flag is the
    difference between a citation and a lead."""
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (json.dumps({"message": {
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
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (json.dumps({"message": {
        "total-results": 678393,
        "items": [{"DOI": "10.x/y", "title": ["Forced to Pursue Science"]}]}}), None))
    s, L = _kit()
    out = cr.search("Forced Science", store=s, limiter=L, use_cache=False)
    m = out.results[0].meta
    assert m["exact"] is False
    assert "fuzzy" in m["verify_note"].lower()


def test_unregistered_doi_is_an_absence_not_a_block(monkeypatch):
    """A 404 on a DOI lookup means the DOI is not registered -- a finding."""
    from evidence_search.core.results import AccessBlocker as AB, Blocker
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (
        None, AB(query="x", mechanism=Blocker.NOT_FOUND, url="u")))
    s, L = _kit()
    out = cr.by_doi("10.9999/nope", store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert "not registered" in out.searched


def test_fedreg_count_is_a_real_total(monkeypatch):
    """Unlike Crossref, this corpus is defined, so the count is a measurement."""
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (json.dumps({
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
    from evidence_search.core.results import AccessBlocker as AB, Blocker
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (
        None, AB(query="x", mechanism=Blocker.NOT_FOUND, url="u")))
    s, L = _kit()
    assert isinstance(fr.search("nothing", store=s, limiter=L, use_cache=False),
                      VerifiedAbsence)

    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: (
        None, AB(query="x", mechanism=Blocker.SERVER_ERROR, url="u", detail="HTTP 503")))
    s2, L2 = _kit()
    out = fr.search("immigration", store=s2, limiter=L2, use_cache=False)
    assert isinstance(out, AccessBlocker) and not isinstance(out, VerifiedAbsence)


# --- federal_register on the shared shell -------------------------------------
# Migrated to run_source. These pin what the migration BOUGHT, so a revert to a
# hand-rolled shell fails rather than silently dropping the auditable record.

def test_fedreg_absence_is_auditable(monkeypatch):
    """A hand-rolled shell returned prose-only negatives. The shell records the
    exact question asked and the boundary of the claim."""
    monkeypatch.setattr(core_http, "fetch",
                        lambda *a, **k: (json.dumps({"count": 0, "results": []}), None))
    s, L = _kit()
    out = fr.search("no such rule", store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert len(out.probes) == 1
    p = out.probes[0]
    assert p.query == "no such rule" and p.source == "federal_register"
    assert p.endpoint and p.corpus
    assert out.not_searched, "the claim must state what it did not cover"


def test_fedreg_parse_failure_is_never_an_absence(monkeypatch):
    """A payload shape change is not evidence that a thing does not exist."""
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: ("<html>not json</html>", None))
    s, L = _kit()
    out = fr.search("x", store=s, limiter=L, use_cache=False)
    assert isinstance(out, AccessBlocker)
    assert not isinstance(out, VerifiedAbsence)


def test_fedreg_replays_from_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: calls.append(1) or (
        json.dumps({"count": 1, "results": [{"document_number": "d", "title": "t",
                                             "html_url": "https://fr.gov/x"}]}), None))
    s, L = _kit()
    fr.search("q", store=s, limiter=L)
    out = fr.search("q", store=s, limiter=L)
    assert len(calls) == 1, "second call should have replayed from cache"
    assert out.coverage.cache_hits == 1


# --- crossref on the shared shell ---------------------------------------------

def test_crossref_title_absence_declares_itself_weak(monkeypatch):
    """The module docstring is emphatic that title search is fuzzy. Before the
    migration a zero came back as bare prose; the claim must carry its own
    limits, because a differently-worded title would not have been found."""
    monkeypatch.setattr(core_http, "fetch",
                        lambda *a, **k: (json.dumps({"message": {"items": []}}), None))
    s, L = _kit()
    out = cr.search("no such paper", store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence)
    assert out.probes and out.probes[0].exact_match_supported is False
    assert not out.is_absolute, "a fuzzy negative must never read as absolute"
    assert any("fuzzy" in c.lower() for c in out.caveats)
    assert out.not_searched


def test_crossref_parse_failure_is_never_an_absence(monkeypatch):
    """A payload that stopped parsing is not evidence the DOI is unregistered."""
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: ("<html>nope</html>", None))
    s, L = _kit()
    out = cr.by_doi("10.1/x", store=s, limiter=L, use_cache=False)
    assert isinstance(out, AccessBlocker) and not isinstance(out, VerifiedAbsence)


def test_crossref_doi_lookup_is_marked_exact(monkeypatch):
    """DOI lookup is authoritative, so its absence CAN be absolute."""
    monkeypatch.setattr(core_http, "fetch",
                        lambda *a, **k: (json.dumps({"message": {"DOI": "10.1/x",
                                                     "title": ["T"]}}), None))
    s, L = _kit()
    out = cr.by_doi("10.1/x", store=s, limiter=L, use_cache=False)
    assert isinstance(out, Hit) and out.results[0].meta["exact"] is True


def test_crossref_caches_doi_longer_than_title(monkeypatch):
    """Bibliographic metadata is stable; a fuzzy result set is not."""
    calls = []
    monkeypatch.setattr(core_http, "fetch", lambda *a, **k: calls.append(1) or (
        json.dumps({"message": {"DOI": "10.1/x", "title": ["T"]}}), None))
    s, L = _kit()
    cr.by_doi("10.1/x", store=s, limiter=L)
    cr.by_doi("10.1/x", store=s, limiter=L)
    assert len(calls) == 1, "second DOI lookup should have replayed from cache"
