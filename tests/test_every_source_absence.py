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

import pathlib
import tempfile
import zipfile

import pytest

from evidence_search.core.results import AccessBlocker, Blocker, RateLimited, VerifiedAbsence


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


def _courtlistener_docket(**kw):
    from evidence_search.sources import courtlistener
    return courtlistener.docket("dcd:1:26-cv-01352", **kw)


def _propublica(**kw):
    from evidence_search.sources import propublica_disclosures
    return propublica_disclosures.search("anything", **kw)


def _oscn(**kw):
    from evidence_search.sources import oscn
    return oscn.search("caddo", lname="frazier", **kw)


def _docs(**kw):
    from evidence_search.sources import docs
    return docs.search("anything", **kw)


def _lda(**kw):
    from evidence_search.sources import lda
    return lda.search(client_name="anything", **kw)


def _fec(**kw):
    from evidence_search.sources import fec
    cache_dir = pathlib.Path(tempfile.mkdtemp()) / "fec_cache"
    return fec.between("C0AAAAAAAA", "C0BBBBBBBB", 2008, 2008, cache_dir=cache_dir, **kw)


def _samgov(**kw):
    from evidence_search.sources import samgov
    return samgov.search("anything", **kw)


def _nonprofit(**kw):
    from evidence_search.sources import nonprofit
    return nonprofit.search("anything", **kw)


def _sec_fulltext(**kw):
    from evidence_search.sources import sec
    return sec.fulltext("anything", **kw)


def _sec_submissions(**kw):
    from evidence_search.sources import sec
    return sec.submissions("0001070985", **kw)


def _sanctions(**kw):
    from evidence_search.sources import sanctions
    cache_dir = pathlib.Path(tempfile.mkdtemp()) / "sanctions_cache"
    return sanctions.search("anything", cache_dir=cache_dir, **kw)


def _fara(**kw):
    from evidence_search.sources import fara
    cache_dir = pathlib.Path(tempfile.mkdtemp()) / "fara_cache"
    return fara.search("anything", cache_dir=cache_dir, **kw)


def _wayback(**kw):
    from evidence_search.sources import wayback
    return wayback.search("https://example.gov/contract", **kw)


def _openalex(**kw):
    from evidence_search.sources import openalex
    return openalex.search("anything", **kw)


def _socrata(**kw):
    from evidence_search.sources import socrata
    return socrata.query("data.cityofnewyork.us", "abcd-1234", "anything", **kw)


def _arcgis(**kw):
    from evidence_search.sources import arcgis
    return arcgis.query("https://gis.example.gov/arcgis/rest/services/Parcels/FeatureServer/0",
                        "anything", field="OWNER", **kw)


def _kev(**kw):
    from evidence_search.sources import kev
    return kev.search("anything", **kw)


def _crtsh(**kw):
    from evidence_search.sources import crtsh
    return crtsh.search("anything.example.gov", **kw)


def _epa(**kw):
    from evidence_search.sources import epa
    return epa.search("anything", **kw)


def _openfda(**kw):
    from evidence_search.sources import openfda
    return openfda.search("anything", **kw)


def _cfpb(**kw):
    from evidence_search.sources import cfpb
    return cfpb.search("anything", **kw)


def _icij(**kw):
    from evidence_search.sources import icij
    cache_dir = pathlib.Path(tempfile.mkdtemp()) / "icij_cache"
    return icij.search("anything", cache_dir=cache_dir, **kw)


def _brokers(**kw):
    from evidence_search.sources import brokers
    cache_dir = pathlib.Path(tempfile.mkdtemp()) / "brokers_cache"
    return brokers.search("anything", state="CA", cache_dir=cache_dir, **kw)


def _gdelt(**kw):
    from evidence_search.sources import gdelt
    return gdelt.search("anything", **kw)


def _montana_cadastral(**kw):
    from evidence_search.core.registry import get_spec
    return get_spec("montana_cadastral").run("anything", **kw)


def _nyc_contracts(**kw):
    from evidence_search.core.registry import get_spec
    return get_spec("nyc_contracts").run("anything", **kw)


def _chicago_contracts(**kw):
    from evidence_search.core.registry import get_spec
    return get_spec("chicago_contracts").run("anything", **kw)


def _usa_search(**kw):
    from evidence_search.sources import usaspending
    return usaspending.search("anything", **kw)


def _usa_counts(**kw):
    from evidence_search.sources import usaspending
    return usaspending.counts("anything", **kw)


def _usa_dollar_sum(**kw):
    from evidence_search.sources import usaspending
    kw.pop("use_cache", None)
    return usaspending.dollar_sum("anything", **kw)


def _usa_idv_orders(**kw):
    from evidence_search.sources import usaspending
    return usaspending.idv_orders("70CMSW26D00000016", **kw)


def _usa_transactions(**kw):
    from evidence_search.sources import usaspending
    return usaspending.transactions("70CDCR26FR0000001", **kw)


def _datagov_catalog(**kw):
    from evidence_search.core.registry import get_spec
    return get_spec("datagov_catalog").run("anything", **kw)


def _web(**kw):
    from evidence_search.engines import web
    return web.search("anything", provider="mwmbl", **kw)


def _searxng(**kw):
    from evidence_search.engines import searxng
    return searxng.search("anything", base="http://searxng.invalid", **kw)


def _news_rss(**kw):
    from evidence_search.engines import news_rss
    return news_rss.search("anything", **kw)


def _patch_fetch(monkeypatch, fn):
    """Replace the GET transport by every door a source reaches it through."""
    from evidence_search.core.conformance import iter_fetch_modules
    for mod in iter_fetch_modules():
        if hasattr(mod, "fetch"):
            monkeypatch.setattr(mod, "fetch", fn)


#: (label, callable). Every public entry point that can return an Outcome.
ENTRY_POINTS = [
    ("web.search", _web),
    ("searxng.search", _searxng),
    ("news_rss.search", _news_rss),
    ("crossref.search", _crossref_search),
    ("crossref.by_doi", _crossref_by_doi),
    ("federal_register.search", _fedreg),
    ("courtlistener.search", _courtlistener),
    ("courtlistener.docket", _courtlistener_docket),
    ("propublica_disclosures.search", _propublica),
    ("oscn.search", _oscn),
    ("docs.search", _docs),
    ("lda.search", _lda),
    ("fec.between", _fec),
    ("samgov.search", _samgov),
    ("nonprofit.search", _nonprofit),
    ("sec.fulltext", _sec_fulltext),
    ("sec.submissions", _sec_submissions),
    ("sanctions.search", _sanctions),
    ("fara.search", _fara),
    ("wayback.search", _wayback),
    ("openalex.search", _openalex),
    ("socrata.query", _socrata),
    ("arcgis.query", _arcgis),
    ("kev.search", _kev),
    ("crtsh.search", _crtsh),
    ("epa.search", _epa),
    ("openfda.search", _openfda),
    ("cfpb.search", _cfpb),
    ("icij.search", _icij),
    ("brokers.search", _brokers),
    ("gdelt.search", _gdelt),
    ("montana_cadastral.run", _montana_cadastral),
    ("nyc_contracts.run", _nyc_contracts),
    ("chicago_contracts.run", _chicago_contracts),
    ("datagov_catalog.run", _datagov_catalog),
    ("usaspending.search", _usa_search),
    ("usaspending.counts", _usa_counts),
    ("usaspending.dollar_sum", _usa_dollar_sum),
    ("usaspending.idv_orders", _usa_idv_orders),
    ("usaspending.transactions", _usa_transactions),
]


def test_every_registered_source_is_in_entry_points():
    """The registry and ENTRY_POINTS must never drift apart."""
    from evidence_search.core.registry import all_specs
    covered_prefixes = {label.split(".")[0] for label, _ in ENTRY_POINTS}
    alias_map = {
        "news": "news_rss",
        "propublica": "propublica_disclosures",
        "fedreg": "federal_register",
    }
    for spec in all_specs():
        mod_name = alias_map.get(spec.name, spec.name)
        assert mod_name in covered_prefixes, (
            f"Registered source {spec.name!r} ({mod_name}) has no entry in ENTRY_POINTS")


@pytest.fixture
def no_network(monkeypatch):
    """Make every outbound path fail, by whichever door the source uses."""
    def _boom(*a, **kw):
        raise AssertionError("test attempted a real network call")
    _patch_fetch(monkeypatch, _boom)
    return monkeypatch


@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_transport_failure_is_never_an_absence(label, call, monkeypatch):
    """A source that could not reach its endpoint knows nothing about the world."""
    def _blocked(url, *, source, query="", **kw):
        return None, AccessBlocker(query=query, mechanism=Blocker.SERVER_ERROR,
                                   url=url, detail="simulated transport failure")

    _patch_fetch(monkeypatch, _blocked)
    import evidence_search.sources.usaspending as us
    monkeypatch.setattr(us, "_post", lambda p, pl: (None, ("http", "simulated failure")))
    import evidence_search.sources.fec as fec_src
    monkeypatch.setattr(fec_src, "_download", lambda base, cycle, cache_dir, timeout=180.0: (
        None, AccessBlocker(query=f"{base}{cycle}", mechanism=Blocker.SERVER_ERROR,
                            url="u", detail="simulated transport failure")))
    import evidence_search.sources.sanctions as sn_src
    monkeypatch.setattr(sn_src, "download_bulk_file", lambda url, dest, **kw: (
        None, AccessBlocker(query="q", mechanism=Blocker.SERVER_ERROR,
                            url=url, detail="simulated transport failure")))

    out = call(use_cache=False)
    assert not isinstance(out, VerifiedAbsence), (
        f"{label} certified an absence over a failed retrieval: {out}")


@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_rate_limited_is_never_an_absence(label, call, monkeypatch):
    """A throttled source is tooling-limited, never content-exhausted."""
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


@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_malformed_success_payload_is_never_an_absence(label, call, monkeypatch):
    """A 200 whose body we cannot read is a parse failure, not an empty corpus."""
    def _garbage(url, *, source, query="", **kw):
        return "<!doctype html><html><body>\x00\x01 not the expected shape", None

    _patch_fetch(monkeypatch, _garbage)
    import evidence_search.sources.usaspending as us
    monkeypatch.setattr(us, "_post", lambda p, pl: (
        {"results": [{"Award ID": "W1", "Recipient Name": "ACME",
                      "Award Amount": "5,000,000"}],
         "page_metadata": {"hasNext": False}}, None))

    import evidence_search.sources.fec as fec_src
    _gz = pathlib.Path(tempfile.mkdtemp()) / "garbage.zip"
    with zipfile.ZipFile(_gz, "w") as _z:
        _z.writestr("itpas2.txt", "not|the|right|shape\n" * 5)
    monkeypatch.setattr(fec_src, "_download",
                        lambda base, cycle, cache_dir, timeout=180.0: (_gz, None))

    import evidence_search.sources.sanctions as sn_src
    _gcsv = pathlib.Path(tempfile.mkdtemp()) / "garbage.csv"
    _gcsv.write_text("<!doctype html><html>not a sanctions csv</html>")
    monkeypatch.setattr(sn_src, "download_bulk_file",
                        lambda url, dest, **kw: (_gcsv, None))

    try:
        out = call(use_cache=False)
    except (ValueError, KeyError, TypeError, AttributeError):
        return
    assert not isinstance(out, VerifiedAbsence), (
        f"{label} certified an absence over an unreadable payload: {out}")


_WELL_FORMED_NON_ANSWERS = {
    "json-error-detail": '{"detail": "Invalid cursor"}',
    "json-errors": '{"errors": {"term": "is invalid"}}',
    "json-empty-object": "{}",
    "json-bare-list": "[]",
    "json-null": "null",
    "json-message-only": '{"message": {"status": "error"}}',
    "sveltekit-no-result": ('{"nodes": [{"type": "data", "data": '
                            '[{"q": 1}, "anything"]}]}'),
    "html-challenge": ("<html><head><title>Just a moment...</title></head><body>"
                       + "<p>Verifying you are human. This may take a few seconds.</p>" * 40
                       + "</body></html>"),
    "html-maintenance": ("<html><body>" + "<p>We are down for maintenance.</p>" * 40
                         + "</body></html>"),
    "xml-not-a-feed": '<?xml version="1.0"?><error><message>bad</message></error>',
    "empty-body": "",
}


@pytest.mark.filterwarnings("ignore:It looks like you're using an HTML parser")
@pytest.mark.parametrize("body", list(_WELL_FORMED_NON_ANSWERS.values()),
                         ids=list(_WELL_FORMED_NON_ANSWERS))
@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_wellformed_non_answer_is_never_an_absence(label, call, body, monkeypatch):
    """A 200 that parses and is not the zero-results shape is not an absence."""
    import json as _json

    _patch_fetch(monkeypatch, lambda url, *, source, query="", **kw: (body, None))
    import evidence_search.sources.usaspending as us
    try:
        decoded = _json.loads(body)
    except ValueError:
        decoded = {"unexpected": body[:40]}
    monkeypatch.setattr(us, "_post", lambda p, pl: (decoded, None))

    import evidence_search.sources.fec as fec_src
    _gz = pathlib.Path(tempfile.mkdtemp()) / "nonanswer.zip"
    with zipfile.ZipFile(_gz, "w") as _z:
        _z.writestr("itpas2.txt", (body or "bad|row") + "\n")
    monkeypatch.setattr(fec_src, "_download",
                        lambda base, cycle, cache_dir, timeout=180.0: (_gz, None))

    import evidence_search.sources.sanctions as sn_src
    _gcsv = pathlib.Path(tempfile.mkdtemp()) / "nonanswer.csv"
    _gcsv.write_text(body)
    monkeypatch.setattr(sn_src, "download_bulk_file",
                        lambda url, dest, **kw: (_gcsv, None))

    try:
        out = call(use_cache=False)
    except (ValueError, KeyError, TypeError, AttributeError):
        return
    assert not isinstance(out, VerifiedAbsence), (
        f"{label} certified an absence over a non-answer: {out.claim()}")


@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_every_absence_states_its_scope(label, call, monkeypatch):
    """Where a source CAN certify an absence, the claim is never a bare 'Not found'."""
    empties = {
        "web.search": '[]',
        "searxng.search": '{"results": [], "unresponsive_engines": []}',
        "news_rss.search": "<rss><channel><title>x</title></channel></rss>",
        "crossref.search": '{"message": {"total-results": 0, "items": []}}',
        "federal_register.search": '{"count": 0, "results": []}',
        "courtlistener.search": '{"count": 0, "results": []}',
        "courtlistener.docket": '{"count": 0, "results": []}',
        "propublica_disclosures.search": ('{"nodes": [{"type": "data", "data": '
                                          '[{"q": 1, "result": 2}, "anything", []]}]}'),
        "oscn.search": "<html><body>Found No Records</body></html>",
        "docs.search": "- [Hooks](https://code.claude.com/docs/hooks)\n",
        "lda.search": '{"count": 0, "next": null, "previous": null, "results": []}',
        "samgov.search": '{"_embedded": {"results": []}, "page": {"totalElements": 0}}',
        "nonprofit.search": '{"total_results": 0, "organizations": []}',
        "sec.fulltext": '{"hits": {"total": {"value": 0}, "hits": []}}',
        "sec.submissions": '{"name": "ACME CORP", "filings": {"recent": {"accessionNumber": []}}}',
        "fara.search": '{"FOREIGN_PRINCIPALS": {"ROW": []}}',
        "wayback.search": '[["timestamp","original","mimetype","statuscode","digest","length"]]',
        "openalex.search": '{"meta": {"count": 0}, "results": []}',
        "socrata.query": '[{"count": "0"}]',
        "arcgis.query": '{"features": []}',
        "kev.search": '{"title": "CISA KEV", "vulnerabilities": []}',
        "crtsh.search": '[]',
        "epa.search": '{"Results": {"QueryRows": "0", "Facilities": []}}',
        "openfda.search": '{"error": {"code": "NOT_FOUND"}}',
        "cfpb.search": '{"hits": {"total": {"value": 0}, "hits": []}}',
        "icij.search": '{"nodes": [{"node_id": "1", "node_type": "entity", "name": "Unrelated Offshore Ltd"}], "relationships": []}',
        "brokers.search": '{"brokers": [{"state": "CA", "broker_name": "Unrelated Aggregator LLC"}]}',
        "gdelt.search": '{"articles": []}',
        "montana_cadastral.run": '{"features": []}',
        "nyc_contracts.run": '[{"count": "0"}]',
        "chicago_contracts.run": '[{"count": "0"}]',
        "datagov_catalog.run": '{"result": {"count": 0, "results": []}, "count": 0, "results": []}',
    }

    def _fetch(url, *, source, query="", **kw):
        if url.endswith("/config"):
            return '{"engines": [{"name": "google", "enabled": true, "categories": ["general"]}]}', None
        if "crt.sh" in url and "id=1" in url:
            return '[{"id": 1, "common_name": "crt.sh"}]', None
        if "api.mwmbl.org" in url and "s=wikipedia" in url:
            return ('[{"url": "https://en.wikipedia.org/", '
                    '"title": [{"value": "Wikipedia", "is_bold": false}], '
                    '"extract": [{"value": "Free encyclopedia", "is_bold": false}]}]'), None
        return empties.get(label, "{}"), None

    _patch_fetch(monkeypatch, _fetch)
    import evidence_search.sources.usaspending as us
    monkeypatch.setattr(us, "_post", lambda p, pl: (
        {"results": ({g: 0 for g in us.AWARD_GROUPS} if "count" in p else []),
         "page_metadata": {"hasNext": False}}, None))

    import evidence_search.sources.fec as fec_src
    _ez_pas = pathlib.Path(tempfile.mkdtemp()) / "empty_pas2.zip"
    _ez_oth = pathlib.Path(tempfile.mkdtemp()) / "empty_oth.zip"
    with zipfile.ZipFile(_ez_pas, "w") as _z:
        _z.writestr("itpas2.txt", "")
    with zipfile.ZipFile(_ez_oth, "w") as _z:
        _z.writestr("itoth.txt", "")
    monkeypatch.setattr(
        fec_src, "_download",
        lambda base, cycle, cache_dir, timeout=180.0: ((_ez_pas if base == "pas2" else _ez_oth), None))

    import evidence_search.sources.sanctions as sn_src
    _ofac_csv = pathlib.Path(tempfile.mkdtemp()) / "ofac.csv"
    _ofac_csv.write_text('36,"AEROCARIBBEAN AIRLINES",-0-,"CUBA",-0-,-0-,-0-,-0-,-0-,-0-,-0-,-0-\n')
    _eu_csv = pathlib.Path(tempfile.mkdtemp()) / "eu.csv"
    _eu_csv.write_text("Entity_LogicalId;NameAlias_WholeName;Entity_Regulation_Programme\n1001;IVANOV IVAN;UKRAINE\n")
    monkeypatch.setattr(
        sn_src, "download_bulk_file",
        lambda url, dest, **kw: ((_ofac_csv if "SDN.CSV" in url else _eu_csv), None))

    out = call(use_cache=False)
    if label == "crossref.by_doi":
        return
    assert isinstance(out, VerifiedAbsence), (
        f"{label} did not recognise its own zero-results shape: "
        f"{type(out).__name__} {getattr(out, 'detail', '')}")
    assert out.probes, f"{label}: an absence with no record of what was asked"
    assert out.not_searched or out.caveats, f"{label}: an absence with no stated limit"
    assert out.claim() != "Not found"


# --- Issue #17: an award ID must be counted in the group it belongs to ------

def _count_post(monkeypatch, hits_by_group: dict[str, int]):
    import evidence_search.sources.usaspending as us
    posted: list[dict] = []

    def _post(path, body):
        posted.append(body)
        asked = set(body["filters"]["award_type_codes"])
        res = {g: (hits_by_group.get(g, 0) if asked & set(codes) else 0)
               for g, codes in us.AWARD_GROUPS.items()}
        return {"results": res}, None

    monkeypatch.setattr(us, "_post", _post)
    return posted


def test_idv_piid_count_is_not_an_absence(monkeypatch):
    import evidence_search.sources.usaspending as us
    _count_post(monkeypatch, {"idvs": 1})

    out = us.counts("70CMSW26D00000016", by_recipient=False,
                    award_types=us.CONTRACT_TYPES, use_cache=False)
    assert not isinstance(out, VerifiedAbsence), (
        f"an IDV PIID was certified absent from a contracts-only count: {out}")
    assert out.results[0].meta["total_awards"] == 1


def test_count_names_the_award_groups_it_did_not_search(monkeypatch):
    import evidence_search.sources.usaspending as us
    _count_post(monkeypatch, {})

    out = us.counts("NO SUCH VENDOR", award_types=us.CONTRACT_TYPES,
                    use_cache=False)
    assert isinstance(out, VerifiedAbsence), type(out)
    excluded = " ".join(out.not_searched)
    for group in ("idvs", "grants", "loans", "direct_payments", "other_assistance"):
        assert group in excluded, f"{group} not named in not_searched: {out.not_searched}"
    assert "contracts" not in excluded.replace("--all-types", "")

    full = us.counts("NO SUCH VENDOR", award_types=us.ALL_AWARD_TYPES,
                     use_cache=False)
    assert isinstance(full, VerifiedAbsence), type(full)
    assert not any("award-type groups" in s for s in full.not_searched), (
        "a count over every group must not claim to have skipped one")


@pytest.mark.parametrize("piid", ["70CMSW26R00000016", "70CMSW26Q00000016"])
@pytest.mark.parametrize("entry", ["counts", "search", "dollar_sum"])
def test_solicitation_number_is_never_an_absence(monkeypatch, piid, entry):
    import evidence_search.sources.usaspending as us
    posted = _count_post(monkeypatch, {})

    kw = {} if entry == "dollar_sum" else {"use_cache": False}
    out = getattr(us, entry)(piid, by_recipient=False, **kw)
    assert isinstance(out, AccessBlocker), type(out)
    assert out.mechanism is Blocker.WRONG_ID_TYPE
    assert "SAM.gov" in out.detail and "solicitation" in out.detail.lower()
    assert not posted, "a solicitation number should not reach the API"


@pytest.mark.parametrize("query", ["70CDCR26FR0000001", "ACME DEFENSE LLC",
                                   "70CMSW26DO", "LOCKHEEDMARTIN"])
def test_non_solicitation_queries_still_reach_the_api(monkeypatch, query):
    import evidence_search.sources.usaspending as us
    posted = _count_post(monkeypatch, {"contracts": 2})

    out = us.counts(query, by_recipient=False, award_types=us.CONTRACT_TYPES,
                    use_cache=False)
    assert posted, f"{query!r} never reached the API"
    assert not isinstance(out, VerifiedAbsence)
