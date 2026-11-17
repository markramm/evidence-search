"""Cross-process rate limiting: the holes found after 2026-11-03.

Parallel agents starved a shared limit that day while each was individually
under it. Five gaps let that happen, and each has tests here:

1. `extract`, `browser` and the browser retry inside run_source fetched without
   reserving a slot.
2. An upstream 429 was known only to the process that received it.
3. `--wait` covered sub-second spacing only, so contended callers were all
   refused at once and retried together.
4. A refusal caused by one agent's bad request counted toward a backoff that
   every agent then sat through.
5. `limits` showed usage and nothing about why a call was being refused.

The multi-process tests start real interpreters against one temporary store.
HTTP is stubbed in every one of them; nothing here reaches a network.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time

import pytest

import evidence_search
import evidence_search.core.limits as limits_mod
from evidence_search import cli
from evidence_search.cli import main
from evidence_search.core import browser as core_browser
from evidence_search.core import http as core_http
from evidence_search.core.http import retry_after_seconds
from evidence_search.core.limits import Limiter, bucket_for_url
from evidence_search.core.results import AccessBlocker, Blocker, RateLimited
from evidence_search.core.store import Store
from evidence_search.sources import federal_register, oscn

REPO = str(pathlib.Path(evidence_search.__file__).resolve().parent.parent)


def _db() -> pathlib.Path:
    return pathlib.Path(tempfile.mkdtemp()) / "t.db"


def _kit():
    s = Store(_db())
    return s, Limiter(s)


def _spawn(script: str, db: pathlib.Path, *args: str, **env) -> subprocess.Popen:
    """Start a real interpreter running `script` against the store at `db`.

    The child imports the package from the same tree as this test (REPO), and
    is pointed at the temporary store through EVIDENCE_DB as well as by
    argument, so nothing it does can land in a real ledger.
    """
    child_env = {**os.environ, "PYTHONPATH": REPO, "EVIDENCE_DB": str(db),
                 "EVIDENCE_SEARCH_LIMITS_FILE": str(db.parent / "no-limits.conf"), **env}
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(script), str(db), *args],
                            env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)


def _finish(proc: subprocess.Popen, timeout: float = 90) -> dict:
    out, err = proc.communicate(timeout=timeout)
    assert proc.returncode == 0, f"child failed:\n{err}"
    return json.loads(out.strip().splitlines()[-1])


def _no_sleep(monkeypatch) -> list[float]:
    """Record sleeps requested by the limiter instead of taking them."""
    slept: list[float] = []

    def fake_sleep(seconds):
        slept.append(seconds)
        # A limiter that waits on something it should refuse would otherwise
        # spin here until its (unslept) deadline, hanging the suite.
        assert len(slept) <= 50, f"the limiter keeps waiting: {slept[:5]}..."
    monkeypatch.setattr(limits_mod.time, "sleep", fake_sleep)
    return slept


# --------------------------------------------------------------------------
# 2. An upstream 429 is shared
# --------------------------------------------------------------------------

_CHILD_429 = """
    import json, sys, httpx
    from evidence_search.core.limits import Limiter
    from evidence_search.core.store import Store
    from evidence_search.sources import federal_register

    calls = []
    def fake_get(url, **kw):
        calls.append(url)
        return httpx.Response(429, headers={"Retry-After": "120"},
                              request=httpx.Request("GET", url))
    httpx.get = fake_get

    store = Store(sys.argv[1])
    out = federal_register.search("anything", store=store, limiter=Limiter(store),
                                  use_cache=False)
    print(json.dumps({"outcome": type(out).__name__, "retry": out.retry_after_s,
                      "detail": out.detail, "http_calls": len(calls)}))
"""


def test_a_429_seen_by_one_process_refuses_the_next_process():
    """THE hole: the next process reserved successfully and hit the same wall."""
    db = _db()
    first = _finish(_spawn(_CHILD_429, db))
    assert first == {"outcome": "RateLimited", "retry": 120, "detail": "HTTP 429",
                     "http_calls": 1}

    second = _finish(_spawn(_CHILD_429, db))
    assert second["outcome"] == "RateLimited"
    assert second["http_calls"] == 0, "the second process repeated the request"
    assert 100 < second["retry"] <= 120, "the refusal must carry the real retry-after"
    assert "429" in second["detail"] and "block" in second["detail"]

    # And the parent, a third process, sees it in the ledger it shares.
    allowed, retry, why = Limiter(Store(db)).reserve("federal_register")
    assert not allowed and 100 < retry <= 120 and "429" in why


@pytest.mark.parametrize("header, expected", [
    ("120", 120),                                   # delay-seconds
    ("0", 1),                                       # never "retry immediately"
    ("Wed, 21 Oct 2015 07:30:00 GMT", 120),         # HTTP-date, 2 minutes after `now`
    ("Wed, 21 Oct 2015 07:00:00 GMT", 1),           # a date already past
    (None, core_http.DEFAULT_RETRY_AFTER_S),        # absent
    ("soon", core_http.DEFAULT_RETRY_AFTER_S),      # unreadable
    ("999999999", core_http.MAX_RETRY_AFTER_S),     # capped
    ("Fri, 01 Jan 2099 00:00:00 GMT", core_http.MAX_RETRY_AFTER_S),
])
def test_retry_after_in_both_forms_with_a_default_and_a_cap(header, expected):
    now = 1445412480.0      # Wed, 21 Oct 2015 07:28:00 GMT
    assert retry_after_seconds(header, now=now) == expected


def test_fetch_honours_an_http_date_retry_after(monkeypatch):
    """The date form used to fall through to the 60s default."""
    import httpx
    from email.utils import formatdate
    when = formatdate(time.time() + 600, usegmt=True)
    monkeypatch.setattr(core_http.httpx, "get", lambda url, **k: httpx.Response(
        429, headers={"Retry-After": when}, request=httpx.Request("GET", url)))
    _, out = core_http.fetch("https://example.gov/x", source="docs")
    assert isinstance(out, RateLimited) and 590 <= out.retry_after_s <= 600


def test_a_block_outlives_an_in_flight_success_and_clears_on_the_next_one():
    s, L = _kit()
    L.note_outcome("crossref", RateLimited(query="q", source="crossref", retry_after_s=50,
                                           detail="HTTP 429"))
    assert not L.reserve("crossref")[0]

    # A success while the block stands belongs to a request sent before the
    # 429: nothing could have been reserved since. It must not reopen the gate.
    L.note_outcome("crossref", None)
    allowed, retry, why = L.reserve("crossref")
    assert not allowed and "429" in why and 40 < retry <= 50

    # Once the deadline passes the block is inert, and the next success
    # removes it from the store.
    s.conn.execute("UPDATE blocks SET until=? WHERE source='crossref'", (time.time() - 1,))
    assert L.reserve("crossref")[0], "an expired block must not refuse"
    L.note_outcome("crossref", None)
    assert s.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0] == 0


def test_a_second_429_never_shortens_the_block():
    s, L = _kit()
    L.note_outcome("crossref", RateLimited(query="q", retry_after_s=300, detail="HTTP 429"))
    L.note_outcome("crossref", RateLimited(query="q", retry_after_s=5, detail="HTTP 429"))
    assert s.blocked_until("crossref")[0] - time.time() > 290


def test_a_timeout_is_not_a_429_and_blocks_nobody():
    s, L = _kit()
    L.note_outcome("crossref", RateLimited(query="q", retry_after_s=30, detail="timeout: read"))
    assert s.blocked_until("crossref") == (0.0, "")
    assert L.backoff_remaining("crossref")[2] == "timeout", "the reason must be specific"


def test_the_usaspending_transport_tuple_shares_its_429_too():
    s, L = _kit()
    L.note_outcome("usaspending", ("rate", "45"))
    allowed, retry, why = L.reserve("usaspending")
    assert not allowed and 35 < retry <= 45 and "429" in why


def test_a_blocked_reservation_leaves_no_row_in_the_ledger():
    s, L = _kit()
    s.block_until("crossref", time.time() + 60, "HTTP 429")
    assert not L.reserve("crossref")[0]
    assert s.count_calls("crossref", 3600) == 0


# --------------------------------------------------------------------------
# 3. Bounded wait, jitter, fairness
# --------------------------------------------------------------------------

_CHILD_WAITER = """
    import json, sys, time
    from evidence_search.core.limits import Limiter
    from evidence_search.core.store import Store

    L = Limiter(Store(sys.argv[1]))
    L.wait_max_s = 60
    time.sleep(max(0.0, float(sys.argv[2]) - time.time()))   # start together
    t0 = time.time()
    allowed, retry, why = L.reserve("docs")
    print(json.dumps({"allowed": allowed, "why": why, "waited": time.time() - t0}))
"""


def test_waiters_in_separate_processes_all_get_a_slot_and_never_exceed_the_window():
    """Fairness without a queue: N waiters, a 3-per-2s window, one ledger.

    Every waiter must eventually be granted, and no 2-second span of the
    ledger may hold more than 3 calls -- checked on the rows themselves, not
    on what the waiters report.
    """
    db = _db()
    Store(db)
    n, per, window = 8, 3, 2.0
    start = time.time() + 1.5
    procs = [_spawn(_CHILD_WAITER, db, str(start), EVIDENCE_SEARCH_LIMITS=f"docs = {per}/2s")
             for _ in range(n)]
    results = [_finish(p) for p in procs]

    assert all(r["allowed"] for r in results), (
        f"a waiter was starved: {[r['why'] for r in results if not r['allowed']]}")
    assert max(r["waited"] for r in results) > 1.0, (
        "nobody waited: the window was not actually contended")

    at = [row[0] for row in Store(db).conn.execute(
        "SELECT at FROM calls WHERE source='docs' ORDER BY at")]
    assert len(at) == n
    for i in range(len(at) - per):
        assert at[i + per] - at[i] >= window, (
            f"{per + 1} calls inside {window}s: {at[i:i + per + 1]}")


def test_waiters_do_not_wake_together(monkeypatch):
    """Each sleep is the refusal's retry-after plus a random extra."""
    s, L = _kit()
    L.wait_max_s = 30
    slept = _no_sleep(monkeypatch)
    answers = iter([(False, 1.0, "docs: 3/3 calls in 60s window", "window:60")] * 6
                   + [(True, None, "", "")])
    monkeypatch.setattr(L, "_attempt", lambda *a: next(answers))

    assert L.reserve("docs") == (True, None, "")
    assert len(slept) == 6
    assert all(1.0 <= x <= 1.0 + 0.35 for x in slept), slept
    assert len(set(slept)) > 1, "identical sleeps: there is no jitter"


def test_a_long_window_is_never_waited_out_and_reports_when_it_reopens(monkeypatch):
    """An hour window with --wait-max must return at once, with the reopen time."""
    s, L = _kit()
    L.wait_max_s = 7200           # even a budget that could cover it
    slept = _no_sleep(monkeypatch)
    opened = time.time() - 600    # the hour's 50 calls began ten minutes ago
    for i in range(50):
        s.conn.execute("INSERT INTO calls (source, at) VALUES ('courtlistener', ?)",
                       (opened + i,))

    t0 = time.time()
    allowed, retry, why = L.reserve("courtlistener")
    assert not allowed and time.time() - t0 < 1.0
    assert slept == [], f"slept on an hour window: {slept}"
    assert "3600s window" in why
    assert 2990 <= retry <= 3000, f"retry-after should be when the window reopens, got {retry}"


def test_the_window_that_reopens_last_is_the_one_reported(monkeypatch):
    """Minute AND hour full: saying "come back in 60s" would be a wrong answer,
    and a waiter would sleep a minute to be refused again."""
    s, L = _kit()
    L.wait_max_s = 120
    slept = _no_sleep(monkeypatch)
    for _ in range(50):
        s.record_call("courtlistener")
    allowed, retry, why = L.reserve("courtlistener")
    assert not allowed and "3600s window" in why and retry > 3500
    assert slept == []


def test_a_wait_that_cannot_fit_the_budget_is_not_started(monkeypatch):
    s, L = _kit()
    L.wait_max_s = 5
    slept = _no_sleep(monkeypatch)
    s.block_until("crossref", time.time() + 60, "HTTP 429")
    allowed, retry, _ = L.reserve("crossref")
    assert not allowed and 50 < retry <= 60 and slept == []


def test_the_session_cap_is_never_waited_out_and_says_when_it_reopens(monkeypatch):
    s, L = _kit()
    L.wait_max_s = 7200
    slept = _no_sleep(monkeypatch)
    for _ in range(8):
        s.record_call("oscn")
    allowed, retry, why = L.reserve("oscn")
    assert not allowed and "session cap" in why and slept == []
    assert 1700 < retry <= 1800, "a session cap refusal should say when a slot reopens"


def test_without_wait_max_a_refusal_is_immediate_as_before(monkeypatch):
    s, L = _kit()
    slept = _no_sleep(monkeypatch)
    for _ in range(25):
        s.record_call("docs")
    assert not L.reserve("docs")[0]
    assert slept == [], "the default must not wait"


@pytest.mark.real_spacing
def test_wait_max_absorbs_real_spacing():
    s, L = _kit()
    L.wait_max_s = 10
    assert L.reserve("usaspending")[0]
    t0 = time.time()
    assert L.reserve("usaspending")[0], "spacing should be waited out"
    assert 0.3 < time.time() - t0 < 3.0


def test_wait_max_is_a_global_flag_in_either_position(capsys):
    assert main(["--wait-max", "2", "limits"]) == 0
    assert main(["limits", "--wait-max", "2"]) == 0
    assert main(["limits", "--wait-max=2"]) == 0
    with pytest.raises(SystemExit) as e:
        main(["--wait-max", "-1", "limits"])
    assert e.value.code == 2


# --------------------------------------------------------------------------
# 1. extract / browser / browser escalation reserve before they fetch
# --------------------------------------------------------------------------

PAGE = "<html><body><p>" + "A served page of real prose. " * 30 + "</p></body></html>"


@pytest.mark.parametrize("url, bucket", [
    ("https://www.justice.gov/opa/pr/x", "host:justice.gov"),
    ("https://example.gov:8443/a?b=c", "host:example.gov"),
    ("https://www.oscn.net/dockets/GetCaseInformation.aspx?db=caddo", "oscn"),
    ("https://oscn.net/anything", "oscn"),
    ("https://evil-oscn.net/anything", "host:evil-oscn.net"),
    ("https://www.courtlistener.com/api/rest/v4/search/?q=x", "courtlistener"),
    # Pages and stored PDFs do not count against the API budget upstream.
    ("https://www.courtlistener.com/docket/123/x/", "host:courtlistener.com"),
    ("https://storage.courtlistener.com/recap/gov.uscourts.x/1.pdf",
     "host:storage.courtlistener.com"),
    ("https://api.usaspending.gov/api/v2/awards/1/", "usaspending"),
    ("https://www.federalregister.gov/documents/2026/01/01/x", "federal_register"),
    ("https://web.archive.org/web/2020/https://example.com", "wayback"),
    ("https://api.crossref.org/works/10.1/x", "crossref"),
    ("not a url", "host:unknown"),
])
def test_every_url_has_a_bucket(url, bucket):
    assert bucket_for_url(url) == bucket


def _stub_fetch(monkeypatch, result):
    calls = []

    def fake(url, **kw):
        calls.append((url, kw))
        return result(url, kw) if callable(result) else result
    monkeypatch.setattr(core_http, "fetch", fake)
    return calls


def test_extract_reserves_a_slot_in_the_hosts_bucket(monkeypatch, capsys):
    calls = _stub_fetch(monkeypatch, (PAGE, None))
    assert main(["extract", "https://www.example.gov/report", "--text"]) == 0
    assert len(calls) == 1
    assert Store().count_calls("host:example.gov", 60) == 1


def test_extract_is_refused_without_fetching_when_the_host_is_spent(monkeypatch, capsys):
    calls = _stub_fetch(monkeypatch, (PAGE, None))
    monkeypatch.setattr(cli, "FETCH_WAIT_S", 0.0)
    s = Store()
    for _ in range(30):
        s.record_call("host:example.gov")
    assert main(["extract", "https://example.gov/report", "--text"]) == cli.EXIT_RATE_LIMITED
    assert calls == [], "the fetch went out with no slot"
    out = capsys.readouterr().out
    assert "RateLimited" in out and "host:example.gov" in out and "retry after" in out


def test_extract_of_an_oscn_page_is_charged_to_oscn_and_honours_its_session_cap(
        monkeypatch, capsys):
    """The Turnstile threshold counts every fetch, whichever command sent it."""
    calls = _stub_fetch(monkeypatch, (PAGE, None))
    slept = _no_sleep(monkeypatch)
    url = "https://www.oscn.net/dockets/GetCaseInformation.aspx?db=caddo&number=CF-1"
    assert main(["extract", url, "--text"]) == 0
    s = Store()
    assert s.count_calls("oscn", 60) == 1

    for _ in range(7):
        s.record_call("oscn")
    assert main(["extract", url, "--text"]) == cli.EXIT_RATE_LIMITED
    assert len(calls) == 1 and slept == [], "a session cap must refuse at once"
    assert "session cap" in capsys.readouterr().out


def test_extract_of_a_remote_pdf_reserves_for_both_requests(monkeypatch, capsys):
    calls = _stub_fetch(
        monkeypatch, lambda url, kw: ((b"%PDF-1.4 bytes" if kw.get("binary")
                                       else "%PDF-1.4 text"), None))
    monkeypatch.setattr(cli, "_read_pdf", lambda *a, **k: (
        "decoded text", {"source": "text", "chars": 12}, None))
    assert main(["extract", "https://example.gov/a.pdf", "--text"]) == 0
    assert len(calls) == 2
    assert Store().count_calls("host:example.gov", 60) == 2


def test_a_429_on_extract_stops_the_next_extract_of_that_host(monkeypatch, capsys):
    calls = _stub_fetch(monkeypatch, (None, RateLimited(query="q", retry_after_s=90, detail="HTTP 429")))
    slept = _no_sleep(monkeypatch)
    assert main(["extract", "https://example.gov/a", "--text"]) == cli.EXIT_RATE_LIMITED
    assert main(["extract", "https://example.gov/b", "--text"]) == cli.EXIT_RATE_LIMITED
    assert len(calls) == 1, "the second extract repeated a request the host had refused"
    assert slept == [], "90s does not fit the default wait, so it must not be started"
    # Another host is untouched.
    calls2 = _stub_fetch(monkeypatch, (PAGE, None))
    assert main(["extract", "https://other.example.org/a", "--text"]) == 0 and len(calls2) == 1


def test_a_walled_page_never_puts_extract_into_backoff(monkeypatch, capsys):
    """A 403 must keep answering as the named blocker, so --browser stays the
    next move. A backoff would replace it with "RateLimited, retry"."""
    wall = AccessBlocker(query="q", mechanism=Blocker.CLOUDFLARE, detail="HTTP 403")
    calls = _stub_fetch(monkeypatch, (None, wall))
    for _ in range(4):
        assert main(["extract", "https://example.gov/a", "--text"]) == cli.EXIT_ACCESS_BLOCKER
    assert len(calls) == 4

    # Nor does a walled OSCN page feed the oscn SEARCH backoff.
    for _ in range(3):
        main(["extract", "https://www.oscn.net/x", "--text"])
    assert Limiter(Store()).backoff_remaining("oscn")[1] == 0


def _stub_browser(monkeypatch, result=(PAGE, None)):
    calls = []

    def fake(url, **kw):
        calls.append(url)
        return result
    monkeypatch.setattr(core_browser, "fetch", fake)
    return calls


def test_the_browser_command_reserves_first(monkeypatch, capsys):
    calls = _stub_browser(monkeypatch)
    url = "https://www.oscn.net/dockets/Results.aspx?db=caddo&lname=Frazier"
    assert main(["browser", url]) == cli.EXIT_HIT
    s = Store()
    assert len(calls) == 1 and s.count_calls("oscn", 60) == 1

    for _ in range(7):
        s.record_call("oscn")
    assert main(["browser", url]) == cli.EXIT_RATE_LIMITED
    assert len(calls) == 1, "the browser was launched with no slot"


def test_extract_via_browser_reserves_first(monkeypatch, capsys):
    calls = _stub_browser(monkeypatch)
    s = Store()
    for _ in range(8):
        s.record_call("oscn")
    assert main(["extract", "https://www.oscn.net/x", "--browser", "--text"]) \
        == cli.EXIT_RATE_LIMITED
    assert calls == []


def test_a_host_the_allow_list_refuses_spends_no_slot(capsys):
    """It is never contacted, so there is nothing to pace."""
    assert main(["browser", "https://not-allowed.example.com/x"]) == cli.EXIT_ACCESS_BLOCKER
    assert Store().count_calls("host:not-allowed.example.com", 3600) == 0


TURNSTILE = AccessBlocker(query="q", mechanism=Blocker.TURNSTILE, detail="HTTP 403")


def test_the_browser_escalation_inside_a_search_claims_its_own_slot(monkeypatch):
    _stub_fetch(monkeypatch, (None, TURNSTILE))
    browsed = _stub_browser(monkeypatch, ("<html>Found No Records</html>", None))
    s, L = _kit()
    oscn.search("caddo", lname="Frazier", store=s, limiter=L, use_cache=False)
    assert len(browsed) == 1
    assert s.count_calls("oscn", 60) == 2, "the browser's request was not in the ledger"


def test_the_browser_escalation_is_refused_at_the_session_cap(monkeypatch):
    _stub_fetch(monkeypatch, (None, TURNSTILE))
    browsed = _stub_browser(monkeypatch)
    s, L = _kit()
    for _ in range(7):
        s.record_call("oscn")          # the plain fetch takes the 8th and last
    out = oscn.search("caddo", lname="Frazier", store=s, limiter=L, use_cache=False)
    assert browsed == [], "the browser went out past the session cap"
    assert isinstance(out, RateLimited) and "session cap" in out.detail
    assert "cloudflare-turnstile" in out.detail, "say what the plain fetch hit, too"
    assert out.coverage.rate_limited == ["oscn:browser"]


def test_the_browser_escalation_is_not_stopped_by_the_failure_it_answers(monkeypatch):
    """Two walled plain fetches start a backoff; the browser retry for the
    second is the remedy and must still run."""
    _stub_fetch(monkeypatch, (None, TURNSTILE))
    browsed = _stub_browser(monkeypatch, ("<html>Found No Records</html>", None))
    s, L = _kit()
    L.note_failure("oscn", "cloudflare-turnstile")
    oscn.search("caddo", lname="Frazier", store=s, limiter=L, use_cache=False)
    assert L.backoff_remaining("oscn")[0] > 0
    assert len(browsed) == 1


# --------------------------------------------------------------------------
# 4. Only genuine failures feed the shared backoff
# --------------------------------------------------------------------------

def _blocker(mech, detail):
    return AccessBlocker(query="q", mechanism=mech, detail=detail)


@pytest.mark.parametrize("blocked", [
    _blocker(Blocker.HTTP_STATUS, "HTTP 400"),
    _blocker(Blocker.HTTP_STATUS, "HTTP 422"),
    _blocker(Blocker.NOT_FOUND, "HTTP 404"),
    _blocker(Blocker.AUTH_WALL, "HTTP 401"),
    _blocker(Blocker.WRONG_ID_TYPE, "that is a PIID"),
    # usaspending's transport labels every status SERVER_ERROR; the status in
    # the detail is what says whose fault it was.
    _blocker(Blocker.SERVER_ERROR, "HTTP 400: bad filter"),
    ("status", "HTTP 422: unprocessable"),
])
def test_our_own_bad_request_does_not_count_toward_backoff(blocked):
    s, L = _kit()
    for _ in range(5):
        L.note_outcome("federal_register", blocked)
    assert L.backoff_remaining("federal_register") == (0.0, 0, "")
    assert L.reserve("federal_register")[0]


@pytest.mark.parametrize("blocked, reason", [
    (_blocker(Blocker.SERVER_ERROR, "HTTP 503"), "http-5xx"),
    (_blocker(Blocker.SERVER_ERROR, "connection refused"), "http-5xx"),
    (_blocker(Blocker.FORBIDDEN, "HTTP 403"), "http-403"),
    (_blocker(Blocker.CLOUDFLARE, "HTTP 403"), "cloudflare-managed-challenge"),
    (_blocker(Blocker.AWS_WAF, "HTTP 202 -- soft block"), "aws-waf-challenge"),
    (RateLimited(query="q", retry_after_s=1, detail="HTTP 429"), "HTTP 429"),
    (RateLimited(query="q", retry_after_s=30, detail="timeout: read"), "timeout"),
    (("status", "HTTP 503: unavailable"), "status"),
    (("timeout", "read"), "timeout"),
])
def test_a_genuine_failure_still_counts_and_keeps_its_reason(blocked, reason):
    s, L = _kit()
    L.note_outcome("federal_register", blocked)
    L.note_outcome("federal_register", blocked)
    remaining, streak, why = L.backoff_remaining("federal_register")
    assert streak == 2 and remaining > 0 and why == reason


def test_a_bad_request_neither_clears_nor_extends_a_real_streak():
    """The source answered, but about our request: it is not a success either."""
    s, L = _kit()
    L.note_failure("crossref", "http-5xx")
    L.note_outcome("crossref", _blocker(Blocker.HTTP_STATUS, "HTTP 400"))
    assert L.backoff_remaining("crossref")[1] == 1


def test_a_run_of_400s_through_a_real_source_leaves_it_open_to_other_agents(monkeypatch):
    """End to end: the 400 that main now reports as http-unexpected-status."""
    import httpx
    sent = []

    def fake_get(url, **kw):
        sent.append(url)
        return httpx.Response(400, text='{"detail": "Invalid cursor"}',
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(core_http.httpx, "get", fake_get)
    s, L = _kit()
    for i in range(4):
        out = federal_register.search(f"q{i}", store=s, limiter=L, use_cache=False)
        assert isinstance(out, AccessBlocker) and out.mechanism is Blocker.HTTP_STATUS
    assert len(sent) == 4, "the source was put into backoff by our own bad requests"
    assert L.backoff_remaining("federal_register")[1] == 0


def test_a_status_declared_an_answer_is_still_a_success(monkeypatch):
    """main passes None for an `answer_on` status; that must clear a streak."""
    import httpx
    monkeypatch.setattr(core_http.httpx, "get", lambda url, **k: httpx.Response(
        404, text="", request=httpx.Request("GET", url)))
    s, L = _kit()
    L.note_failure("federal_register", "http-5xx")
    federal_register.search("zero results", store=s, limiter=L, use_cache=False)
    assert L.backoff_remaining("federal_register")[1] == 0


# --------------------------------------------------------------------------
# 5. `limits` shows why a call was refused
# --------------------------------------------------------------------------

def _limits_json(capsys) -> dict:
    capsys.readouterr()
    assert main(["--json", "limits"]) == 0
    return {r["source"]: r for r in json.loads(capsys.readouterr().out)}


def test_limits_reports_usage_block_and_backoff_per_source(capsys):
    s = Store()
    L = Limiter(s)
    for _ in range(3):
        L.reserve("crossref")
    for _ in range(3):
        L.note_failure("news_rss", "http-5xx")
    s.block_until("courtlistener", time.time() + 200, "HTTP 429")

    rows = _limits_json(capsys)
    cr = rows["crossref"]
    assert cr["usage"] == [{"window_s": 60, "max": 40, "used": 3, "reopens_in_s": None}]
    assert cr["windows"] == ["3/40 per 60s"], "the old field must keep its shape"
    assert cr["blocked_until"] is None and cr["next_call"] == "allowed"
    assert cr["backoff"] == {"consecutive_failures": 0, "remaining_s": 0.0,
                             "last_reason": None}

    nr = rows["news_rss"]["backoff"]
    assert nr["consecutive_failures"] == 3 and nr["remaining_s"] > 0
    assert nr["last_reason"] == "http-5xx"
    assert "backoff" in rows["news_rss"]["next_call"]

    cl = rows["courtlistener"]
    assert 190 < cl["blocked_for_s"] <= 200 and cl["blocked_reason"] == "HTTP 429"
    assert cl["blocked_until"] > time.time() and "429" in cl["next_call"]

    assert rows["oscn"]["session"] == {"window_s": 1800, "max": 8, "used": 0}


def test_limits_text_says_what_is_refusing_each_source(capsys):
    s = Store()
    L = Limiter(s)
    for _ in range(3):
        L.note_failure("news_rss", "http-5xx")
    s.block_until("courtlistener", time.time() + 200, "HTTP 429")
    for _ in range(40):
        s.record_call("crossref")
    capsys.readouterr()
    assert main(["limits"]) == 0
    out = capsys.readouterr().out

    def section(name):
        return out.split(f"\n{name}\n", 1)[1].split("\n\n", 1)[0]

    assert "blocked:   until " in section("courtlistener")
    assert "REFUSED -- upstream 429 block" in section("courtlistener")
    assert "backoff:   3 consecutive failures (http-5xx)" in section("news_rss")
    assert "REFUSED -- failure backoff" in section("news_rss")
    assert "usage:     40/40 per 60s" in section("crossref")
    assert "REFUSED -- 40/40 in the 60s window, reopens in" in section("crossref")
    assert "blocked:   no" in section("docs") and "next call: allowed" in section("docs")
    assert "backoff:   clear" in section("docs")


def test_limits_lists_the_host_buckets_that_have_been_used(monkeypatch, capsys):
    _stub_fetch(monkeypatch, (PAGE, None))
    main(["extract", "https://www.example.gov/report", "--text"])
    rows = _limits_json(capsys)
    assert rows["host:example.gov"]["usage"][0]["used"] == 1
    assert rows["host:example.gov"]["usage"][0]["max"] == 30


# --------------------------------------------------------------------------
# The entry point another tool can share the ledger through
# --------------------------------------------------------------------------

def test_limits_reserve_claims_a_call_and_refuses_with_13(capsys):
    assert main(["limits", "reserve", "courtlistener"]) == 0
    s = Store()
    assert s.count_calls("courtlistener", 60) == 1

    for _ in range(4):
        s.record_call("courtlistener")
    capsys.readouterr()
    assert main(["--json", "limits", "reserve", "courtlistener"]) == cli.EXIT_RATE_LIMITED
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["outcome"] == "RateLimited" and refusal["source"] == "courtlistener"
    assert 0 < refusal["retry_after_s"] <= 60
    assert s.count_calls("courtlistener", 60) == 5, "a refusal must not be recorded"


def test_limits_reserve_accepts_a_url_and_rejects_a_typo(capsys):
    assert main(["limits", "reserve", "https://www.courtlistener.com/api/rest/v4/dockets/1/"]) == 0
    assert Store().count_calls("courtlistener", 60) == 1
    assert main(["limits", "reserve", "courtlistner"]) == cli.EXIT_USAGE
    assert "unknown source" in capsys.readouterr().err
    assert main(["limits", "reserve"]) == cli.EXIT_USAGE


def test_limits_block_records_a_429_another_tool_received(capsys):
    assert main(["limits", "block", "courtlistener", "75"]) == 0
    allowed, retry, why = Limiter(Store()).reserve("courtlistener")
    assert not allowed and 65 < retry <= 75 and "429" in why
