"""Regression tests for the guarantee the tool exists to provide.

The 2026-08-19 session exhausted a 200-call budget because ~12 parallel workers
each assumed they owned it. These tests fail against the pre-fix limiter.
"""
import pathlib
import tempfile
import threading

from cascade_search.core.limits import Limiter
from cascade_search.core.store import Store


def _db():
    return pathlib.Path(tempfile.mkdtemp()) / "t.db"


def test_reservation_is_atomic_under_parallel_workers():
    """THE original bug: N workers must not collectively exceed the window.

    Brave is the probe because it has no min_interval_s -- other sources are
    accidentally serialised by their spacing, which MASKS the race rather than
    preventing it.
    """
    db = _db()
    Store(db)
    n_workers, limit = 40, 20          # brave: 20 per 1s
    barrier = threading.Barrier(n_workers)
    granted, lock = [], threading.Lock()

    def worker(i):
        L = Limiter(Store(db))
        barrier.wait()                 # force genuine simultaneity
        if L.reserve("brave")[0]:
            with lock:
                granted.append(i)

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n_workers)]
    [t.start() for t in ts]
    [t.join() for t in ts]

    assert len(granted) <= limit, (
        f"{len(granted)} workers granted against a {limit}/s limit -- "
        "check() and record() are not atomic")
    assert granted, "reservation deadlocked: nobody got through"


def test_session_cap_is_shared_across_processes():
    """OSCN's cap exists to stay under the ~10-fetch Turnstile threshold.

    An in-memory per-Limiter counter cannot do that under fan-out: the cap must
    live in the shared ledger or it protects nothing.
    """
    db = _db()
    first = Limiter(Store(db))
    for _ in range(8):                 # session_max for oscn
        first.record("oscn")

    second = Limiter(Store(db))        # a different worker process
    allowed, _, why = second.check("oscn")
    assert not allowed, "session cap not enforced across processes"
    assert "session cap" in why


def test_reserve_rolls_back_when_over_limit():
    """A refused reservation must not leave a phantom call in the ledger."""
    db = _db()
    s = Store(db)
    L = Limiter(s)
    for _ in range(20):
        L.reserve("brave")
    before = s.count_calls("brave", 1.0)
    assert not L.reserve("brave")[0]
    assert s.count_calls("brave", 1.0) == before, "refused reservation still recorded a call"


def test_wait_covers_spacing_but_never_a_budget_window():
    """--wait may absorb sub-second spacing; it must NOT hide a real limit.

    Sleeping out a 50/hr cap would turn an informative RateLimited into a hang,
    which is exactly the ambiguity this tool exists to remove.
    """
    db = _db()
    L = Limiter(Store(db))
    L.wait_for_spacing = True

    assert L.reserve("news_rss")[0]
    assert L.reserve("news_rss")[0], "0.5s spacing should be absorbed by --wait"

    # Exhaust courtlistener's 5/min budget window; --wait must still refuse.
    L2 = Limiter(Store(_db()))
    L2.wait_for_spacing = True
    for _ in range(5):
        L2.store.record_call("courtlistener")
    allowed, retry, why = L2.reserve("courtlistener")
    assert not allowed, "--wait must never wait out a budget window"
    assert "window" in why


def test_backoff_escalates_on_a_run_of_failures_not_a_blip():
    """A source that is hard-down should not be retried on a healthy schedule.

    brave and startpage failed on every query across an afternoon of real work;
    fixed windows retried them identically each time, spending budget to relearn
    what the previous calls had established.
    """
    L = Limiter(Store(_db()))
    assert L.backoff_remaining("news_rss")[0] == 0

    L.note_failure("news_rss", "CAPTCHA")
    assert L.backoff_remaining("news_rss")[0] == 0, "one failure is a blip"

    for _ in range(3):
        L.note_failure("news_rss", "CAPTCHA")
    wait, streak, reason = L.backoff_remaining("news_rss")
    assert streak == 4 and wait > 0 and "CAPTCHA" in reason

    L.note_success("news_rss")
    assert L.backoff_remaining("news_rss")[0] == 0, "success must reset the streak"


def test_backoff_refuses_the_reservation_before_spending_a_slot():
    """Refuse early: do not burn a ledger slot on a call that has failed 4 times."""
    s = Store(_db())
    L = Limiter(s)
    for _ in range(4):
        L.note_failure("news_rss", "CAPTCHA")
    allowed, retry, why = L.reserve("news_rss")
    assert not allowed and "backing off" in why
    assert s.count_calls("news_rss", 60) == 0, "no ledger row for a refused call"


def test_backoff_is_capped():
    L = Limiter(Store(_db()))
    for _ in range(40):
        L.note_failure("news_rss", "down")
    assert L.backoff_remaining("news_rss")[0] <= L.policy("news_rss").backoff_max_s
