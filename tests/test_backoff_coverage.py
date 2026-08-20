"""Every source must feed the escalating-backoff counter.

reserve() consults backoff_remaining() for EVERY source, but the counter it
reads was fed from exactly one place -- run_source() -- so the four sources
predating that shell (crossref, federal_register, usaspending, searxng) never
backed off no matter how many times they failed. The machinery was fully built
and structurally dead for them.
"""
import pathlib
import tempfile

import pytest

from cascade_search.core.limits import Limiter
from cascade_search.core.results import AccessBlocker, Blocker
from cascade_search.core.store import Store
from cascade_search.engines import searxng
from cascade_search.sources import crossref, federal_register


def _kit():
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    return s, Limiter(s)


BLOCKED = AccessBlocker(query="q", mechanism=Blocker.SERVER_ERROR, detail="boom")


@pytest.mark.parametrize("mod, source, call", [
    (federal_register, "federal_register",
     lambda m, s, L: m.search("q", store=s, limiter=L, use_cache=False)),
    (crossref, "crossref",
     lambda m, s, L: m.search("q", store=s, limiter=L, use_cache=False)),
    (searxng, "searxng",
     lambda m, s, L: m.search("q", store=s, limiter=L, base="http://x", use_cache=False)),
])
def test_a_failing_source_enters_backoff(monkeypatch, mod, source, call):
    """Two failures are a pattern, and the third call must be refused."""
    s, L = _kit()
    monkeypatch.setattr(mod, "fetch", lambda *a, **k: (None, BLOCKED))

    call(mod, s, L)
    assert L.backoff_remaining(source)[1] == 1, "first failure was not recorded"
    call(mod, s, L)
    remaining, streak, reason = L.backoff_remaining(source)
    assert streak == 2 and remaining > 0, f"{source} never entered backoff"
    # The reason is surfaced to the operator, so it must be specific.
    assert reason and reason != "error"

    allowed, _, why = L.reserve(source)
    assert not allowed and "backing off" in why


def test_a_success_clears_the_streak(monkeypatch):
    s, L = _kit()
    monkeypatch.setattr(federal_register, "fetch", lambda *a, **k: (None, BLOCKED))
    federal_register.search("q", store=s, limiter=L, use_cache=False)
    assert L.backoff_remaining("federal_register")[1] == 1

    monkeypatch.setattr(federal_register, "fetch",
                        lambda *a, **k: ('{"results": [], "count": 0}', None))
    federal_register.search("q2", store=s, limiter=L, use_cache=False)
    assert L.backoff_remaining("federal_register")[1] == 0, "a success must clear the streak"


def test_note_outcome_keeps_the_reason_from_a_transport_tuple():
    """usaspending._post predates the outcome-object shape and returns a plain
    ("rate"|"status"|..., detail) tuple. getattr() alone flattened that to
    "error", which tells an operator nothing."""
    s, L = _kit()
    L.note_outcome("usaspending", ("rate", "60"))
    assert L.backoff_remaining("usaspending")[2] == "rate"


def test_note_outcome_never_raises_into_the_caller():
    """Bookkeeping must not convert a good answer into an error."""
    class Exploding:
        def record_failure(self, *a, **k): raise RuntimeError("ledger down")
        def clear_failures(self, *a, **k): raise RuntimeError("ledger down")
        def failure_state(self, *a, **k): return (0, 0.0, "")

    L = Limiter(Exploding())
    L.note_outcome("x", BLOCKED)   # must not raise
    L.note_outcome("x", None)
