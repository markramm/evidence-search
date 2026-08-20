"""The shared source shell.

Every source repeated this logic, which is why a shell bug had to be fixed once
per source -- and why the next source someone added inherited whichever bug the
template currently carried.
"""
import pathlib
import tempfile

from evidence_search.core.limits import Limiter
from evidence_search.core.results import (AccessBlocker, Hit, RateLimited,
                                         Result, VerifiedAbsence)
from evidence_search.core.source import run_source
from evidence_search.core.store import Store


def _kit():
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    return s, Limiter(s)


def _run(monkeypatch, body, parse, **kw):
    import evidence_search.core.http as http
    monkeypatch.setattr(http, "fetch", lambda *a, **k: (body, None))
    s, L = _kit()
    return run_source("q", source="news_rss", url="https://x/y", searched="probe",
                      parse=parse, store=s, limiter=L, **kw)


def test_empty_parse_is_a_verified_absence(monkeypatch):
    out = _run(monkeypatch, "<xml/>", lambda b: [])
    assert isinstance(out, VerifiedAbsence)
    assert out.coverage.is_clean


def test_parse_failure_is_never_an_absence(monkeypatch):
    """A shape change means we could not read the answer -- not that there isn't one."""
    def boom(body):
        raise ValueError("shape changed")
    out = _run(monkeypatch, "{}", boom)
    assert isinstance(out, AccessBlocker)
    assert not isinstance(out, VerifiedAbsence)


def test_verify_refusal_blocks_a_false_success(monkeypatch):
    """ProPublica's unfiltered-index failure: looks like a broad hit, isn't."""
    out = _run(monkeypatch, "body",
               lambda b: [Result(url="u", title="t")],
               verify=lambda b: "filter was NOT applied")
    assert isinstance(out, AccessBlocker)
    assert "NOT applied" in out.detail


def test_rate_limit_short_circuits_before_fetch(monkeypatch):
    import evidence_search.core.http as http
    called = []
    monkeypatch.setattr(http, "fetch", lambda *a, **k: called.append(1) or ("x", None))
    s, L = _kit()
    for _ in range(20):            # exhaust news_rss 20/60s
        s.record_call("news_rss")
    out = run_source("q", source="news_rss", url="https://x/y", searched="probe",
                     parse=lambda b: [], store=s, limiter=L)
    assert isinstance(out, RateLimited)
    assert not called, "must not spend a fetch once the budget is gone"


def test_poisoned_payload_is_served_but_not_cached(monkeypatch):
    """A cache-write refusal must not corrupt coverage.

    Coverage records WHO ANSWERED. Marking it errored would flip is_clean and
    downgrade a legitimate result -- conflating a storage problem with a
    retrieval problem.
    """
    rows = [Result(url="https://x/?search=q", title="(unnamed appointee)") for _ in range(35)]
    out = _run(monkeypatch, "body", lambda b: rows)
    assert isinstance(out, Hit) and len(out.results) == 35
    assert out.coverage.is_clean
    assert out.coverage.cache_write_refused


def test_escalation_coverage_names_both_attempts(monkeypatch):
    """A gate must show the plain fetch AND the browser both failed.

    Naming only one attempt lets a reader assume the other path was never
    tried -- the ambiguity typed outcomes exist to remove.
    """
    import evidence_search.core.http as http
    import evidence_search.core.browser as browser
    from evidence_search.core.results import AccessBlocker, AwaitingHuman, Blocker

    monkeypatch.setattr(http, "fetch", lambda *a, **k: (
        None, AccessBlocker(query="q", mechanism=Blocker.TURNSTILE, url="u")))
    monkeypatch.setattr(browser, "fetch", lambda *a, **k: (
        None, AwaitingHuman(query="q", url="u", resume_token="tok")))

    s, L = _kit()
    out = run_source("q", source="oscn", url="https://www.oscn.net/x", searched="probe",
                     parse=lambda b: [], store=s, limiter=L, escalate=True)
    assert isinstance(out, AwaitingHuman)
    assert out.coverage.queried == ["oscn", "oscn:browser"]
    assert set(out.coverage.errored) == {"oscn", "oscn:browser"}


def test_escalation_recovers_when_the_browser_passes(monkeypatch):
    import evidence_search.core.http as http
    import evidence_search.core.browser as browser
    from evidence_search.core.results import AccessBlocker, Blocker

    monkeypatch.setattr(http, "fetch", lambda *a, **k: (
        None, AccessBlocker(query="q", mechanism=Blocker.TURNSTILE, url="u")))
    monkeypatch.setattr(browser, "fetch", lambda *a, **k: ("<html>ok</html>", None))

    s, L = _kit()
    out = run_source("q", source="oscn", url="https://www.oscn.net/x", searched="probe",
                     parse=lambda b: [Result(url="u", title="t")],
                     store=s, limiter=L, escalate=True)
    assert isinstance(out, Hit)


def test_sentinel_absence_is_distinct_from_an_empty_parse(monkeypatch):
    """OSCN says 'Found No Records' in an otherwise normal page.

    That is a publishable negative. A parser that merely found no rows might
    instead mean the page shape changed, so the two must not be conflated.
    """
    out = _run(monkeypatch, "<html>Found No Records</html>",
               lambda b: [Result(url="u", title="never reached")],
               absent_when=lambda b: "Found No Records" in b)
    assert isinstance(out, VerifiedAbsence)
    assert out.coverage.is_clean


def test_archive_on_retrieval_writes_hash_and_manifest(monkeypatch, tmp_path):
    from evidence_search.core import archive as arch
    monkeypatch.setattr(arch, "DEFAULT_ARCHIVE", tmp_path)
    out = _run(monkeypatch, "<html>case page</html>",
               lambda b: [Result(url="u", title="t")],
               archive_as="oscn-caddo-CF-2013-00038.html")
    assert isinstance(out, Hit)
    assert (tmp_path / "oscn-caddo-CF-2013-00038.html").exists()
    assert (tmp_path / "MANIFEST-evidence-search.txt").exists()
