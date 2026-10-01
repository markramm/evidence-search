"""Senate LDA (lda.gov/api/v1): bill-name search, substring-name caveats, the
absence invariant.

The regression fixture (`tests/fixtures_lda_purdue_2008.json`) is a real, trimmed
API response fetched 2026-09-29 from
`https://lda.gov/api/v1/filings/?client_name=Purdue+Pharma&filing_year=2008` --
the known case that got LDA re-probed out of Tier 5 (#19): Purdue Pharma LP's
own 2008 Q1 and Q2 LD-2 filings name "National Pain Care Policy Act (H.R. 2994)".
"""
from __future__ import annotations

import json
import pathlib
import tempfile

import pytest

from evidence_search.core import http as core_http
from evidence_search.core.limits import Limiter
from evidence_search.core.results import AccessBlocker, Hit, RateLimited, VerifiedAbsence
from evidence_search.core.store import Store
from evidence_search.sources import lda

FIXTURE = json.loads(
    (pathlib.Path(__file__).parent / "fixtures_lda_purdue_2008.json").read_text())


def _kit():
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    return s, Limiter(s)


def _one_page(payload):
    """A fetch() stand-in that serves `payload` once, then an empty page."""
    calls = []

    def fetch(url, *, source, query="", **kw):
        calls.append(url)
        return json.dumps(payload), None
    return fetch, calls


# --- bill-number normalization ------------------------------------------------

@pytest.mark.parametrize("raw,expect", [
    ("H.R. 2994", ("HR", "2994")),
    ("H.R.2994", ("HR", "2994")),
    ("HR 2994", ("HR", "2994")),
    ("hr2994", ("HR", "2994")),
    ("  H. R. 2994  ", ("HR", "2994")),
    ("S. 2160", ("S", "2160")),
    ("S2160", ("S", "2160")),
    ("H.Res. 123", ("HRES", "123")),
    ("S.Res. 45", ("SRES", "45")),
])
def test_bill_parsing_normalizes_spacing_and_periods(raw, expect):
    assert lda._parse_bill(raw) == expect


@pytest.mark.parametrize("raw", ["not a bill", "", "Pain Care Act", "HR"])
def test_unparseable_bill_is_none(raw):
    assert lda._parse_bill(raw) is None


def test_bill_pattern_matches_real_description_text():
    pattern = lda._bill_pattern("HR", "2994")
    text = "National Pain Care Policy Act (H.R. 2994) of 2007"
    assert pattern.search(text)
    # Must not match a different, longer bill number sharing the prefix.
    assert not pattern.search("H.R. 29945")


# --- the regression case: Purdue Pharma LP, 2008 Q1+Q2, H.R. 2994 ------------

def test_purdue_pharma_2008_names_hr_2994(monkeypatch):
    """The exact case that justified re-probing LDA out of Tier 5 (#19)."""
    fetch, calls = _one_page(FIXTURE)
    monkeypatch.setattr(core_http, "fetch", fetch)
    s, L = _kit()

    out = lda.search(client_name="Purdue Pharma", years=[2008], bill="H.R. 2994",
                     store=s, limiter=L, use_cache=False)

    assert isinstance(out, Hit), out
    assert len(out.results) == 2
    periods = {r.meta["filing_period"] for r in out.results}
    assert periods == {"first_quarter", "second_quarter"}
    for r in out.results:
        assert r.meta["client"] == "PURDUE PHARMA, LP" or "PURDUE" in r.meta["client"].upper()
        assert r.meta["registrant"] == "PURDUE PHARMA, LP"
        assert any("2994" in (a["description"] or "") for a in r.meta["matched_activities"])
    assert len(calls) == 1, "one page should have answered a 2-filing result"


def test_purdue_pharma_wrong_bill_is_a_scoped_absence(monkeypatch):
    """Same filings, a bill they do NOT name -- must be the scoped negative."""
    fetch, _ = _one_page(FIXTURE)
    monkeypatch.setattr(core_http, "fetch", fetch)
    s, L = _kit()

    out = lda.search(client_name="Purdue Pharma", years=[2008], bill="H.R. 9999",
                     store=s, limiter=L, use_cache=False)

    assert isinstance(out, VerifiedAbsence), out
    assert "H.R. 9999" in out.searched
    assert "Purdue Pharma" in out.searched
    assert "2008" in out.searched
    for item in ("LD-203", "paper-era", str(lda.COVERAGE_START_YEAR)):
        assert any(item in n for n in out.not_searched), (item, out.not_searched)
    assert out.probes and out.probes[0].result_count == 0
    assert not out.is_absolute, "a substring-matched name search must never read as absolute"


def test_no_filings_at_all_is_an_absence(monkeypatch):
    """No --bill: a client/year pair with zero filings is itself a negative."""
    fetch, _ = _one_page({"count": 0, "next": None, "previous": None, "results": []})
    monkeypatch.setattr(core_http, "fetch", fetch)
    s, L = _kit()

    out = lda.search(client_name="No Such Lobbying Client At All", years=[2008],
                     store=s, limiter=L, use_cache=False)
    assert isinstance(out, VerifiedAbsence), out
    assert "no LD-2 filings" in out.searched


def test_listing_without_bill_returns_every_filing(monkeypatch):
    fetch, _ = _one_page(FIXTURE)
    monkeypatch.setattr(core_http, "fetch", fetch)
    s, L = _kit()

    out = lda.search(client_name="Purdue Pharma", years=[2008], store=s, limiter=L,
                     use_cache=False)
    assert isinstance(out, Hit)
    assert len(out.results) == 2


# --- absence invariant --------------------------------------------------------

def test_transport_failure_is_never_an_absence(monkeypatch):
    def _blocked(url, *, source, query="", **kw):
        from evidence_search.core.results import AccessBlocker as AB, Blocker
        return None, AB(query=query, mechanism=Blocker.SERVER_ERROR, url=url,
                        detail="simulated transport failure")
    monkeypatch.setattr(core_http, "fetch", _blocked)
    s, L = _kit()
    out = lda.search(client_name="anything", use_cache=False, store=s, limiter=L)
    assert not isinstance(out, VerifiedAbsence)
    assert isinstance(out, AccessBlocker)


def test_rate_limited_is_never_an_absence():
    class _Exhausted:
        def reserve(self, source):
            return False, 60, "simulated budget exhaustion"

        def note_outcome(self, *a, **kw):
            pass

    s, _ = _kit()
    out = lda.search(client_name="anything", use_cache=False, store=s, limiter=_Exhausted())
    assert isinstance(out, RateLimited)
    assert not isinstance(out, VerifiedAbsence)


def test_malformed_payload_is_never_an_absence(monkeypatch):
    def _garbage(url, *, source, query="", **kw):
        return "<!doctype html><html><body>not json at all", None
    monkeypatch.setattr(core_http, "fetch", _garbage)
    s, L = _kit()
    out = lda.search(client_name="anything", use_cache=False, store=s, limiter=L)
    assert isinstance(out, AccessBlocker)
    assert not isinstance(out, VerifiedAbsence)


def test_bad_bill_format_is_a_blocker_not_an_absence():
    s, L = _kit()
    out = lda.search(client_name="anything", bill="not a bill at all",
                     use_cache=False, store=s, limiter=L)
    assert isinstance(out, AccessBlocker)
    assert out.mechanism.value == "wrong-identifier-type"
    assert not isinstance(out, VerifiedAbsence)


def test_pagination_cap_is_named_as_a_partial_read(monkeypatch):
    """Hitting the page cap must never silently present as a complete read."""
    page = {"count": 999, "next": "https://lda.gov/api/v1/filings/?page=2",
            "previous": None, "results": [FIXTURE["results"][0]]}

    def fetch(url, *, source, query="", **kw):
        return json.dumps(page), None
    monkeypatch.setattr(core_http, "fetch", fetch)
    s, L = _kit()

    out = lda.search(client_name="Purdue Pharma", use_cache=False, store=s, limiter=L,
                     max_pages=1)
    assert isinstance(out, Hit)
    assert any("page cap" in c for c in out.results[0].meta["search_caveats"]), (
        "hitting the page cap with more upstream must be named, not silently dropped")


def test_registrant_only_query_is_accepted(monkeypatch):
    fetch, calls = _one_page(FIXTURE)
    monkeypatch.setattr(core_http, "fetch", fetch)
    s, L = _kit()
    out = lda.search(registrant_name="Purdue Pharma", years=[2008], store=s,
                     limiter=L, use_cache=False)
    assert isinstance(out, Hit)
    assert "registrant_name=Purdue" in calls[0] or "registrant_name=Purdue+Pharma" in calls[0]
