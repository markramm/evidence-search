"""The cache must not be trusted more than the network.

Two failures motivated these tests, both observed in production:
  * A transient parse bug wrote 35 ProPublica rows all titled
    "(unnamed appointee)" under a 24h TTL; every later call served them.
  * A cached VerifiedAbsence replayed as freshly publishable, with synthesised
    clean coverage and no indication of when the search actually ran.
"""
import pathlib
import tempfile
import time

import pytest

from cascade_search.core.results import VerifiedAbsence
from cascade_search.core.store import Store, CachePoisoned, looks_degenerate


def _store():
    return Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")


def test_rejects_degenerate_rows_on_write():
    """Every row sharing one placeholder title is the ProPublica poisoning."""
    rows = [{"url": "https://x/?search=q", "title": "(unnamed appointee)"} for _ in range(35)]
    assert looks_degenerate(rows)
    s = _store()
    with pytest.raises(CachePoisoned):
        s.put("k", "propublica_disclosures", rows)
    assert s.get("k") is None, "poisoned payload must not be stored"


def test_allows_legitimately_uniform_small_payloads():
    """Do not over-fire: a single row, or genuinely distinct rows, are fine."""
    assert not looks_degenerate([{"url": "u", "title": "Jared Taylor Isaacman"}])
    assert not looks_degenerate(
        [{"url": f"u{i}", "title": f"Person {i}"} for i in range(35)])
    assert not looks_degenerate([])


def test_cached_absence_carries_its_real_age():
    """A replayed absence must disclose when it was actually established."""
    s = _store()
    s.put("k", "news_rss", [], ttl_s=3600)
    time.sleep(0.05)
    entry = s.get_entry("k")
    assert entry is not None
    payload, fetched_at = entry
    assert payload == []
    assert time.time() - fetched_at >= 0.05


def test_absence_from_cache_reports_cache_age(monkeypatch):
    """The outcome a caller receives says the negative is cached, and how old."""
    from cascade_search.engines import news_rss
    s = _store()
    key = __import__("cascade_search.core.store", fromlist=["cache_key"]).cache_key(
        "news_rss", "nothing here")
    s.put(key, "news_rss", [], ttl_s=3600)

    out = news_rss.search("nothing here", s, None, True)
    assert isinstance(out, VerifiedAbsence)
    assert out.coverage.cache_hits == 1
    assert out.coverage.cache_age_s is not None, "replayed absence must be dated"
    assert "cache" in out.searched.lower()
