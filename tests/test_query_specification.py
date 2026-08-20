"""A negative is an absence of STRINGS. The strings must be as loud as the verdict.

Typed outcomes made execution auditable. They do nothing for specification: the
tool will certify a flawlessly-executed search for the wrong string. The origin
bug had two halves -- wrong corpora, and searching "watch network" instead of
the movement's own name. Typed outcomes fixed the first and not the second.
"""
import io
import time
from contextlib import redirect_stdout

from cascade_search.cli import _emit, _asked_strings
from cascade_search.core.results import Coverage, Probe, VerifiedAbsence


def _absence(**kw):
    probes = kw.pop("probes", None) or [
        Probe(source="oscn", query="Frazier", params={"db": "caddo", "year": 2013},
              corpus="OSCN Caddo docket index", result_count=0,
              exact_match_supported=True, at=time.time())
    ]
    return VerifiedAbsence(
        query=kw.pop("query", "Frazier caddo 2013"),
        coverage=kw.pop("coverage", Coverage(queried=["oscn"], responsive=["oscn"])),
        searched=kw.pop("searched", "OSCN Caddo"),
        probes=probes, not_searched=kw.pop("not_searched", []),
        caveats=kw.pop("caveats", []),
    )


def _render(outcome) -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _emit(outcome, as_json=False)
    return buf.getvalue()


def test_exact_query_string_is_printed():
    assert "'Frazier'" in _render(_absence())


def test_asked_block_precedes_the_verdict():
    """The specification outranks the conclusion. A reader who stops early
    should have seen what was asked, not just that it came back empty."""
    out = _render(_absence())
    assert "ASKED:" in out
    verdict = max(out.find("PUBLISHABLE NEGATIVE"), out.find("BOUNDED NEGATIVE"))
    assert verdict > 0
    assert out.find("ASKED:") < verdict, "the verdict printed before the query it judges"


def test_output_says_the_absence_is_of_strings():
    out = _render(_absence()).lower()
    assert "strings" in out
    assert "different question" in out


def test_scoping_params_travel_with_the_query():
    """`Frazier` in Caddo in 2013 is not `Frazier` statewide."""
    out = _render(_absence())
    assert "db=caddo" in out and "year=2013" in out


def test_empty_params_are_not_shown_as_scope():
    p = Probe(source="oscn", query="Frazier", params={"db": "caddo", "fname": ""},
              result_count=0, at=time.time())
    line = _asked_strings(_absence(probes=[p]))[0]
    assert "fname" not in line, "an empty filter is not a scope"


def test_fuzzy_endpoints_are_flagged_in_the_asked_block():
    """If the endpoint cannot honour exact-phrase matching, the absence is
    weaker than it looks and the ASKED line has to say so."""
    p = Probe(source="news_rss", query="Force Science", params={},
              result_count=0, exact_match_supported=False, at=time.time())
    assert "FUZZY" in _asked_strings(_absence(probes=[p]))[0]


def test_identical_queries_are_deduped():
    p = Probe(source="oscn", query="Frazier", params={"db": "caddo"},
              result_count=0, at=time.time())
    assert len(_asked_strings(_absence(probes=[p, p, p]))) == 1


def test_distinct_queries_are_all_listed():
    a = Probe(source="oscn", query="Frazier", params={}, result_count=0, at=time.time())
    b = Probe(source="oscn", query="Frasier", params={}, result_count=0, at=time.time())
    lines = _asked_strings(_absence(probes=[a, b]))
    assert len(lines) == 2
    assert any("Frazier" in x for x in lines) and any("Frasier" in x for x in lines)


def test_json_carries_asked_at_top_level():
    """A consumer deciding whether to trust a negative must not have to walk
    into probes[] to find what the negative is about."""
    d = _absence().to_dict()
    assert d["asked"][0]["query"] == "Frazier"
    assert d["asked"][0]["params"] == {"db": "caddo", "year": 2013}
    assert "different question" in d["asked_note"]


def test_a_probe_with_no_query_contributes_nothing():
    p = Probe(source="oscn", query="   ", params={}, result_count=0, at=time.time())
    assert _asked_strings(_absence(probes=[p])) == []
