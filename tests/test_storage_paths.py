"""The rename must not silently relocate the ledger.

evidence-search was renamed from cascade-search. The default storage root moved
with it, which orphaned an existing ledger: 639 recorded calls, 705 stored
records and 14 open humanomation gates went quiet in one commit.

That is not a cosmetic loss. The rate limiter enforces a budget that lives in
that ledger, so a fresh empty store reads as "nothing spent yet" and the next
parallel run walks into the upstream 429s the limiter exists to prevent -- and a
rate-limited engine downgrades a VerifiedAbsence to RateLimited, which is the
one claim this tool exists to make.

conftest patches DEFAULT_DB/DEFAULT_ARCHIVE directly, so nothing else here
exercises resolution. These tests do.
"""
import os
from pathlib import Path

import pytest

from evidence_search.core.store import _resolve

NEW, OLD, LEAF = "EVIDENCE_DB", "CASCADE_DB", "store.db"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(NEW, raising=False)
    monkeypatch.delenv(OLD, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _seed(home, name):
    d = home / name
    d.mkdir(parents=True, exist_ok=True)
    (d / LEAF).write_text("x")
    return d / LEAF


def test_fresh_install_uses_the_new_root(home):
    assert _resolve(NEW, OLD, LEAF) == home / ".evidence-search" / LEAF


def test_legacy_store_is_adopted_when_it_is_the_only_one(home):
    legacy = _seed(home, ".cascade-search")
    assert _resolve(NEW, OLD, LEAF) == legacy


def test_new_root_wins_when_both_exist(home):
    _seed(home, ".cascade-search")
    current = _seed(home, ".evidence-search")
    assert _resolve(NEW, OLD, LEAF) == current


def test_explicit_env_overrides_both_roots(home):
    _seed(home, ".cascade-search")
    os.environ[NEW] = "/tmp/explicit.db"
    try:
        assert _resolve(NEW, OLD, LEAF) == Path("/tmp/explicit.db")
    finally:
        del os.environ[NEW]


def test_legacy_env_var_is_still_honoured(home):
    """Renaming the env var must not silently void an existing override."""
    _seed(home, ".cascade-search")
    os.environ[OLD] = "/tmp/legacy-env.db"
    try:
        assert _resolve(NEW, OLD, LEAF) == Path("/tmp/legacy-env.db")
    finally:
        del os.environ[OLD]
