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


def _searxng(**kw):
    from evidence_search.engines import searxng
    return searxng.search("anything", base="http://searxng.invalid", **kw)


def _news_rss(**kw):
    from evidence_search.engines import news_rss
    return news_rss.search("anything", **kw)


def _patch_fetch(monkeypatch, fn):
    """Replace the GET transport by every door a source reaches it through.

    run_source looks `core.http.fetch` up at call time; searxng binds its own
    reference at import, so it needs patching by name as well.
    """
    import evidence_search.core.http as http_mod
    import evidence_search.engines.searxng as searxng_mod
    monkeypatch.setattr(http_mod, "fetch", fn)
    monkeypatch.setattr(searxng_mod, "fetch", fn)


#: (label, callable). Every public entry point that can return an Outcome.
#:
#: searxng and news_rss carry about 98% of real call volume and were outside
#: this sweep until 2026-11-16: the engines live in a different package from
#: the sources, and the list was written from the sources directory.
ENTRY_POINTS = [
    ("searxng.search", _searxng),
    ("news_rss.search", _news_rss),
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
    from evidence_search.core.results import AccessBlocker, Blocker

    def _blocked(url, *, source, query="", **kw):
        return None, AccessBlocker(query=query, mechanism=Blocker.SERVER_ERROR,
                                   url=url, detail="simulated transport failure")

    _patch_fetch(monkeypatch, _blocked)
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


@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_malformed_success_payload_is_never_an_absence(label, call, monkeypatch):
    """A 200 whose body we cannot read is a parse failure, not an empty corpus.

    This is the `dollar_sum` bug generalised: the endpoint answered, the rows
    were there, and our reading of them failed. Reporting that as absence
    publishes a negative about a corpus that in fact had content.

    A source is allowed to return AccessBlocker, RateLimited, or even a Hit
    here. The single forbidden answer is "it isn't there."
    """
    def _garbage(url, *, source, query="", **kw):
        return "<!doctype html><html><body>\x00\x01 not the expected shape", None

    _patch_fetch(monkeypatch, _garbage)
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
    # No exemptions. oscn and docs used to xfail here: an HTML scraper that
    # counted rows read an unrecognised page as zero of them. An absence now
    # needs a positive marker from every source, scrapers included.
    assert not isinstance(out, VerifiedAbsence), (
        f"{label} certified an absence over an unreadable payload: {out}")


#: Bodies that parse cleanly and are not an answer. Each is something a real
#: service sends with a 200: an API's error object, an empty object, a bare
#: list, a bot wall, a feed-shaped document with no channel.
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
    """A 200 that parses and is not the zero-results shape is not an absence.

    The malformed-payload test above feeds bytes no parser accepts. The live
    failures were the opposite: bodies that parse perfectly -- a JSON error
    object, a challenge page -- and simply contain no rows. Zero rows is not
    a finding; only the source's own zero-results shape is.
    """
    import json as _json

    _patch_fetch(monkeypatch, lambda url, *, source, query="", **kw: (body, None))
    import evidence_search.sources.usaspending as us
    try:
        decoded = _json.loads(body)
    except ValueError:
        decoded = {"unexpected": body[:40]}
    monkeypatch.setattr(us, "_post", lambda p, pl: (decoded, None))

    try:
        out = call(use_cache=False)
    except (ValueError, KeyError, TypeError, AttributeError):
        return
    assert not isinstance(out, VerifiedAbsence), (
        f"{label} certified an absence over a non-answer: {out.claim()}")


@pytest.mark.parametrize("label,call", ENTRY_POINTS, ids=[e[0] for e in ENTRY_POINTS])
def test_every_absence_states_its_scope(label, call, monkeypatch):
    """Where a source CAN certify an absence, the claim is never a bare 'Not found'.

    Each source is served its own genuine zero-results shape. Those that return
    an absence must say what was asked (a probe) and at least one limit.
    """
    empties = {
        "searxng.search": '{"results": [], "unresponsive_engines": []}',
        "news_rss.search": "<rss><channel><title>x</title></channel></rss>",
        "crossref.search": '{"message": {"total-results": 0, "items": []}}',
        "federal_register.search": '{"count": 0, "results": []}',
        "courtlistener.search": '{"count": 0, "results": []}',
        "propublica_disclosures.search": ('{"nodes": [{"type": "data", "data": '
                                          '[{"q": 1, "result": 2}, "anything", []]}]}'),
        "oscn.search": "<html><body>Found No Records</body></html>",
        "docs.search": "- [Hooks](https://code.claude.com/docs/hooks)\n",
    }

    def _fetch(url, *, source, query="", **kw):
        if url.endswith("/config"):
            return '{"engines": [{"name": "google", "enabled": true, "categories": ["general"]}]}', None
        return empties.get(label, "{}"), None

    _patch_fetch(monkeypatch, _fetch)
    import evidence_search.sources.usaspending as us
    monkeypatch.setattr(us, "_post", lambda p, pl: (
        {"results": ({g: 0 for g in us.AWARD_GROUPS} if "count" in p else []),
         "page_metadata": {"hasNext": False}}, None))

    out = call(use_cache=False)
    if label == "crossref.by_doi":
        return   # a DOI lookup has no empty shape; its absence is the declared 404
    assert isinstance(out, VerifiedAbsence), (
        f"{label} did not recognise its own zero-results shape: "
        f"{type(out).__name__} {getattr(out, 'detail', '')}")
    assert out.probes, f"{label}: an absence with no record of what was asked"
    assert out.not_searched or out.caveats, f"{label}: an absence with no stated limit"
    assert out.claim() != "Not found"


# --- Issue #17: an award ID must be counted in the group it belongs to ------
#
# `usaspending "70CMSW26D00000016" --keywords --count` exited 11 with a
# VerifiedAbsence. The award exists: it is an IDV (usaspending award 362519505),
# and `--all-types` finds it. The count had searched contracts only, and its
# `not_searched` did not say so, so the negative read as complete. Same
# invariant as the rest of this file: never certify an absence over a search
# that was not the whole of what the caller asked about.


def _count_post(monkeypatch, hits_by_group: dict[str, int]):
    """Fake the count endpoint: each group reports its count only if asked for.

    Records every body posted, so a test can check what was actually searched.
    """
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
    """The live #17 case: an IDV's PIID counted over contracts only.

    The 9th character of a uniform PIID is the instrument type, and `D` is an
    indefinite-delivery vehicle -- an IDV, which lives in a different award
    group from contracts. Counting it among contracts alone and reporting zero
    certified that an award that exists does not.
    """
    import evidence_search.sources.usaspending as us
    _count_post(monkeypatch, {"idvs": 1})

    out = us.counts("70CMSW26D00000016", by_recipient=False,
                    award_types=us.CONTRACT_TYPES, use_cache=False)
    assert not isinstance(out, VerifiedAbsence), (
        f"an IDV PIID was certified absent from a contracts-only count: {out}")
    assert out.results[0].meta["total_awards"] == 1


def test_count_names_the_award_groups_it_did_not_search(monkeypatch):
    """A default count is contracts only. Its absence must say so.

    Without this, `not_searched` listed only the reporting threshold and
    classified spending, and a zero over contracts read as a zero over every
    federal award.
    """
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
    """R and Q in the 9th position mark a solicitation, not an award.

    USAspending holds awards. A solicitation number is not in it by definition,
    so "not found" there says nothing about whether the procurement exists --
    the answer is on SAM.gov. The source must say so without spending a ledger
    slot on a question it knows the corpus cannot answer.
    """
    import evidence_search.sources.usaspending as us
    from evidence_search.core.results import AccessBlocker, Blocker
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
    """The PIID check must not swallow award IDs or ordinary names."""
    import evidence_search.sources.usaspending as us
    posted = _count_post(monkeypatch, {"contracts": 2})

    out = us.counts(query, by_recipient=False, award_types=us.CONTRACT_TYPES,
                    use_cache=False)
    assert posted, f"{query!r} never reached the API"
    assert not isinstance(out, VerifiedAbsence)
