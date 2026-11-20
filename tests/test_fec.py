"""FEC bulk source (#16): local index, both-sides reporting, refund netting,
committee-name collision warnings, and the absence invariant.

The regression fixtures in `tests/fixtures_fec_purdue_rogers.py` are real
bulk-file rows extracted with grep/awk from the published 2026, 2008, 2010 and
2012 cycle files -- not synthesized. Nothing here downloads anything: every
test either pre-populates the local SQLite index directly via `_index_rows`
(the pure, I/O-free entry point `between()`/`lookup_committee()` build on), or
patches `fec._download` to prove a download was never attempted.
"""
from __future__ import annotations

import pathlib
import tempfile
import zipfile

import pytest

from evidence_search.core.limits import Limiter
from evidence_search.core.results import (AccessBlocker, Hit, RateLimited,
                                         VerifiedAbsence)
from evidence_search.core.store import Store
from evidence_search.sources import fec

from fixtures_fec_purdue_rogers import CM_LINES, OTH_LINES, PAS2_LINES

PURDUE_PAC = "C00370643"
ROGERS_MI = "C00343863"      # Rogers for Congress (Michigan)
ROGERS_AL = "C00367862"      # Mike Rogers for Congress (Alabama)
LEADERSHIP_PAC = "C00370791"  # Majority Initiative to Keep Electing Republicans Fund

#: Every (base, cycle) any test below might touch. Fixture cycles are
#: pre-loaded with their real rows; anything else in this set is marked
#: indexed with ZERO rows, so `is_indexed()` is true and `ensure_indexed()`
#: never reaches `_download` for a cycle this suite simply doesn't need.
_ALL_CYCLES = [2006, 2008, 2010, 2012]


def _kit(tmp_path=None):
    tmp = tmp_path or pathlib.Path(tempfile.mkdtemp())
    store = Store(tmp / "store.db")
    limiter = Limiter(store)
    cache_dir = tmp / "fec_cache"
    conn = fec.conn_for(cache_dir)
    return store, limiter, conn, cache_dir


def _preindex(conn, base, lines_by_cycle):
    for cycle, lines in lines_by_cycle.items():
        rows = [fec._parse_row(base, ln) for ln in lines]
        assert all(r is not None for r in rows), f"fixture line failed to parse ({base} {cycle})"
        n = fec._index_rows(conn, base, cycle, rows)
        fec._mark_indexed(conn, base, cycle, n)


@pytest.fixture
def indexed_kit():
    store, limiter, conn, cache_dir = _kit()
    _preindex(conn, "pas2", PAS2_LINES)
    _preindex(conn, "oth", OTH_LINES)
    _preindex(conn, "cm", CM_LINES)
    for base in ("pas2", "oth"):
        for cycle in _ALL_CYCLES:
            if not fec.is_indexed(conn, base, cycle):
                fec._mark_indexed(conn, base, cycle, 0)
    return store, limiter, conn, cache_dir


@pytest.fixture(autouse=True)
def no_downloads(monkeypatch):
    """Structural guarantee, not just an incidental one: if any test below
    reaches the network path, fail loudly instead of silently fetching."""
    def _boom(*a, **kw):
        raise AssertionError("test attempted to download a real FEC bulk file")
    monkeypatch.setattr(fec, "_download", _boom)


# --- cycle parsing -------------------------------------------------------------

@pytest.mark.parametrize("raw,expect", [
    ("2008", (2008, 2008)),
    ("2006-2014", (2006, 2014)),
    (" 2006 - 2014 ", (2006, 2014)),
])
def test_parse_cycle_range(raw, expect):
    assert fec.parse_cycle_range(raw) == expect


@pytest.mark.parametrize("raw", ["not a cycle", "2014-2006", "", "20xx"])
def test_parse_cycle_range_rejects_bad_input(raw):
    with pytest.raises(ValueError):
        fec.parse_cycle_range(raw)


def test_cycles_in_range_rounds_to_even_years():
    assert fec._cycles_in_range(2005, 2010) == [2006, 2008, 2010]
    assert fec._cycles_in_range(2008, 2008) == [2008]


# --- the regression case: Purdue Pharma PAC -> the two Mike Rogers committees -

def test_purdue_to_rogers_michigan_five_thousand_dollar_contributions(indexed_kit):
    """5 x $1,000, 2005-2010 -- the exact case named in #16."""
    store, limiter, conn, cache_dir = indexed_kit
    out = fec.between(PURDUE_PAC, ROGERS_MI, 2005, 2010, store=store, limiter=limiter,
                      cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, Hit), out
    assert len(out.results) == 5
    for r in out.results:
        assert r.meta["amount"] == 1000.0
        assert r.meta["filer_committee"] == PURDUE_PAC
        assert r.meta["counterparty_committee"] == ROGERS_MI
        assert r.meta["image_num"]
        assert r.meta["transaction_id"]
        assert r.meta["net"] == 5000.0
        assert r.meta["gross_contributed"] == 5000.0
        assert r.meta["refunded"] == 0.0
    dates = sorted(r.meta["date"] for r in out.results)
    assert dates == ["02152008", "03272009", "06132005", "08132010", "12032007"]


def test_purdue_to_rogers_alabama_contribution_and_refund_netted(indexed_kit):
    """One $1,000 contribution, one -$1,000 refund -- net zero, BOTH rows shown."""
    store, limiter, conn, cache_dir = indexed_kit
    out = fec.between(PURDUE_PAC, ROGERS_AL, 2008, 2008, store=store, limiter=limiter,
                      cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, Hit), out
    assert len(out.results) == 2, "the refund row must be shown, not dropped"
    amounts = sorted(r.meta["amount"] for r in out.results)
    assert amounts == [-1000.0, 1000.0]
    refund_rows = [r for r in out.results if r.meta["is_refund"]]
    assert len(refund_rows) == 1
    for r in out.results:
        assert r.meta["gross_contributed"] == 1000.0
        assert r.meta["refunded"] == 1000.0
        assert r.meta["net"] == 0.0, "a contribution and its refund must net to zero"


def test_purdue_to_leadership_pac_both_sides_reported(indexed_kit):
    """2006 and 2012: BOTH C00370643 and C00370791 filed their own report."""
    store, limiter, conn, cache_dir = indexed_kit
    out = fec.between(PURDUE_PAC, LEADERSHIP_PAC, 2006, 2012, store=store, limiter=limiter,
                      cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, Hit), out
    assert len(out.results) == 4, "two years, two filers each"
    cycles = sorted({r.meta["cycle"] for r in out.results})
    assert cycles == [2006, 2012]
    filers = {r.meta["filer_committee"] for r in out.results}
    assert filers == {PURDUE_PAC, LEADERSHIP_PAC}
    for r in out.results:
        assert r.meta["both_sides_reported"] is True
    assert out.results[0].meta["net"] == 2000.0


def test_purdue_to_rogers_michigan_is_only_one_side_reported(indexed_kit):
    """pas2 never carries the candidate committee's own matching report --
    verified live by grepping for the reverse row and finding nothing."""
    store, limiter, conn, cache_dir = indexed_kit
    out = fec.between(PURDUE_PAC, ROGERS_MI, 2005, 2010, store=store, limiter=limiter,
                      cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, Hit)
    assert all(r.meta["both_sides_reported"] is False for r in out.results)


# --- absence with no committee pairing ----------------------------------------

def test_unrelated_committee_pair_is_a_scoped_absence(indexed_kit):
    store, limiter, conn, cache_dir = indexed_kit
    out = fec.between(ROGERS_MI, ROGERS_AL, 2005, 2010, store=store, limiter=limiter,
                      cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, VerifiedAbsence), out
    assert ROGERS_MI in out.searched and ROGERS_AL in out.searched
    assert "2005-2010" in out.searched
    joined = " ".join(out.not_searched)
    assert "unitemized" in joined
    assert "only the side that files" in joined
    assert not out.is_absolute


# --- committee-name lookup, warns on multiple IDs -----------------------------

def test_committee_lookup_warns_on_two_mike_rogers(indexed_kit):
    store, limiter, conn, cache_dir = indexed_kit
    out = fec.lookup_committee("Rogers", 2008, 2008, store=store, limiter=limiter,
                               cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, Hit), out
    ids = {r.meta["committee_id"] for r in out.results}
    assert ids == {ROGERS_MI, ROGERS_AL}
    for r in out.results:
        w = r.meta["multiple_committees_warning"]
        assert w and ROGERS_MI in w and ROGERS_AL in w


def test_committee_lookup_single_match_carries_no_warning(indexed_kit):
    store, limiter, conn, cache_dir = indexed_kit
    out = fec.lookup_committee("Purdue Pharma", 2008, 2008, store=store, limiter=limiter,
                               cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, Hit)
    assert len(out.results) == 1
    assert out.results[0].meta["multiple_committees_warning"] is None


def test_committee_lookup_no_match_is_an_absence(indexed_kit):
    store, limiter, conn, cache_dir = indexed_kit
    out = fec.lookup_committee("No Such Committee Name At All", 2008, 2008, store=store,
                               limiter=limiter, cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, VerifiedAbsence)


# --- caching -------------------------------------------------------------------

def test_between_replays_from_cache(indexed_kit, monkeypatch):
    store, limiter, conn, cache_dir = indexed_kit
    out1 = fec.between(PURDUE_PAC, ROGERS_MI, 2005, 2010, store=store, limiter=limiter,
                       cache_dir=cache_dir)
    assert isinstance(out1, Hit)

    def _boom(*a, **kw):
        raise AssertionError("second call should have replayed from cache")
    monkeypatch.setattr(fec, "_rows_between", _boom)

    out2 = fec.between(PURDUE_PAC, ROGERS_MI, 2005, 2010, store=store, limiter=limiter,
                       cache_dir=cache_dir)
    assert out2.coverage.cache_hits == 1
    assert len(out2.results) == 5


# --- the absence invariant: a failed read is never a finding -------------------

def _make_zip(tmp_path, base, lines, zname=None):
    p = tmp_path / (zname or f"{base}.zip")
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr(fec.TXT_NAME[base], "\n".join(lines) + ("\n" if lines else ""))
    return p


def test_download_failure_is_never_an_absence(monkeypatch, tmp_path):
    """Nothing pre-indexed; the one cycle requested fails to download."""
    store, limiter, conn, cache_dir = _kit(tmp_path)

    def _fail(base, cycle, cache_dir, timeout=180.0):
        return None, AccessBlocker(query=f"{base}{cycle}", mechanism=None, url="u",
                                   detail="simulated transport failure")
    monkeypatch.setattr(fec, "_download", _fail)

    out = fec.between("C0AAAAAAAA", "C0BBBBBBBB", 2008, 2008, store=store, limiter=limiter,
                      cache_dir=cache_dir, use_cache=False)
    assert not isinstance(out, VerifiedAbsence)
    assert isinstance(out, AccessBlocker)


def test_rate_limited_download_is_never_an_absence(tmp_path):
    store, limiter, conn, cache_dir = _kit(tmp_path)

    class _Exhausted:
        def reserve(self, source):
            return False, 60, "simulated budget exhaustion"

        def note_outcome(self, *a, **kw):
            pass

    out = fec.between("C0AAAAAAAA", "C0BBBBBBBB", 2008, 2008, store=store, limiter=_Exhausted(),
                      cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, RateLimited)
    assert not isinstance(out, VerifiedAbsence)


def test_bad_zip_is_never_an_absence(monkeypatch, tmp_path):
    store, limiter, conn, cache_dir = _kit(tmp_path)
    garbage = tmp_path / "garbage.zip"
    garbage.write_bytes(b"not actually a zip file")

    def _fake(base, cycle, cache_dir, timeout=180.0):
        return garbage, None
    monkeypatch.setattr(fec, "_download", _fake)

    out = fec.between("C0AAAAAAAA", "C0BBBBBBBB", 2008, 2008, store=store, limiter=limiter,
                      cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, AccessBlocker)
    assert not isinstance(out, VerifiedAbsence)


def test_malformed_bulk_lines_is_never_an_absence(monkeypatch, tmp_path):
    """Every line fails to parse (field count changed) -- a format change,
    not an empty cycle. The FEC does not file zero lines in a real cycle."""
    store, limiter, conn, cache_dir = _kit(tmp_path)
    zpath = _make_zip(tmp_path, "pas2", ["this|is|not|the|right|shape"] * 10)

    def _fake(base, cycle, cache_dir, timeout=180.0):
        return zpath, None
    monkeypatch.setattr(fec, "_download", _fake)

    out = fec.between("C0AAAAAAAA", "C0BBBBBBBB", 2008, 2008, store=store, limiter=limiter,
                      cache_dir=cache_dir, use_cache=False)
    assert isinstance(out, AccessBlocker)
    assert not isinstance(out, VerifiedAbsence)


def test_partial_cycle_failure_downgrades_absence_to_rate_limited(monkeypatch, tmp_path):
    """One of two cycles indexes cleanly with zero matching rows; the other
    cycle fails outright. The UNION has zero rows, but coverage is dirty --
    this must downgrade, exactly like `verified_absence()` is built to do,
    never read as a clean negative across the whole range.
    """
    store, limiter, conn, cache_dir = _kit(tmp_path)
    empty_zip = _make_zip(tmp_path, "pas2", [], zname="empty.zip")
    empty_oth_zip = _make_zip(tmp_path, "oth", [], zname="empty_oth.zip")

    def _fake(base, cycle, cache_dir, timeout=180.0):
        if cycle == 2010:
            return None, AccessBlocker(query=f"{base}{cycle}", mechanism=None, url="u",
                                       detail="simulated failure for 2010")
        return (empty_zip if base == "pas2" else empty_oth_zip), None
    monkeypatch.setattr(fec, "_download", _fake)

    out = fec.between("C0AAAAAAAA", "C0BBBBBBBB", 2008, 2010, store=store, limiter=limiter,
                      cache_dir=cache_dir, use_cache=False)
    assert not isinstance(out, VerifiedAbsence), (
        "a cycle that failed to index must never let the range read as a clean absence")
    assert isinstance(out, RateLimited), type(out)
