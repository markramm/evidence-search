"""USAspending: the countable primitive.

Two workers independently called this "the load-bearing source here" on a
procurement beat and fell back to raw HTTP in its absence.
"""
import pathlib
import tempfile

from cascade_search.core.limits import Limiter
from cascade_search.core.results import Hit, RateLimited, VerifiedAbsence
from cascade_search.core.store import Store
from cascade_search.sources import usaspending as usa


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
    from cascade_search.core.results import AccessBlocker
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
