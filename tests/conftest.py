"""Test isolation: a private store, and no sub-second call spacing.

Two things here are deliberately SHARED global state -- the call ledger (so
parallel workers spend one budget, not one each) and the cache. Both are right
in production and wrong in a test run, where they make the suite spend real
source budget, poison the real cache, and race itself.

The spacing delay (`min_interval_s`) is the other half. It is a politeness
pause between calls, and honouring it in tests meant a real sleep racing a real
clock: the suite passed on a quiet laptop and failed on a loaded CI runner, in
a different test each time. Zeroing it changes no assertion -- the BUDGET
WINDOWS, which are the part with meaning, are left exactly as configured, and
the two tests that assert on spacing itself opt out with @pytest.mark.real_spacing.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

import cascade_search.core.limits as limits_mod


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_spacing: keep min_interval_s as configured (for tests that "
        "assert on the spacing policy itself)")


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch, request):
    import cascade_search.core.archive as archive_mod
    import cascade_search.core.store as store_mod

    monkeypatch.setattr(store_mod, "DEFAULT_DB", tmp_path / "store.db")
    monkeypatch.setattr(archive_mod, "DEFAULT_ARCHIVE", tmp_path / "archive")

    # Keep every real policy -- several tests assert on them directly -- but
    # drop min_interval_s to zero. The BUDGET WINDOWS are the part with
    # meaning; the sub-second spacing is a politeness delay whose only effect
    # in a test run is a real sleep racing a real clock. Zeroing it removes the
    # race without weakening a single assertion about limits.
    if request.node.get_closest_marker("real_spacing"):
        yield
        return

    monkeypatch.setattr(
        limits_mod, "POLICIES",
        {k: replace(v, min_interval_s=0.0) for k, v in limits_mod.POLICIES.items()})
    monkeypatch.setattr(
        limits_mod, "DEFAULT_POLICY",
        replace(limits_mod.DEFAULT_POLICY, min_interval_s=0.0))
    yield
