"""Account-specific rate-limit overrides.

Why this exists: a CourtListener membership raises the documented 5/50/125 to
20/250/1,000, and the encoded policy refused calls the account was entitled to.
"""
import pytest

import evidence_search.core.limits as limits
from evidence_search.core.limits import Limiter, LimitsConfigError, effective_policy, parse_limits
from evidence_search.core.store import Store

TIER3 = "courtlistener = 20/min, 250/hr, 1000/day"


def test_no_override_leaves_the_built_in_policy():
    pol, origin = effective_policy("courtlistener")
    assert origin is None
    assert pol.windows == [(60, 5), (3600, 50), (86400, 125)]


def test_file_override_replaces_windows_and_nothing_else(tmp_path, monkeypatch):
    f = tmp_path / "limits.conf"
    f.write_text("# membership tier 3\n" + TIER3 + "\n")
    monkeypatch.setenv("EVIDENCE_SEARCH_LIMITS_FILE", str(f))
    base = limits.POLICIES["courtlistener"]
    pol, origin = effective_policy("courtlistener")
    assert pol.windows == [(60.0, 20), (3600.0, 250), (86400.0, 1000)]
    assert origin == str(f)
    assert (pol.min_interval_s, pol.backoff_base_s, pol.note) == (
        base.min_interval_s, base.backoff_base_s, base.note)
    assert effective_policy("oscn")[1] is None


def test_variable_wins_over_the_file(tmp_path, monkeypatch):
    f = tmp_path / "limits.conf"
    f.write_text("courtlistener = 6/min\n")
    monkeypatch.setenv("EVIDENCE_SEARCH_LIMITS_FILE", str(f))
    monkeypatch.setenv("EVIDENCE_SEARCH_LIMITS", "courtlistener = 7/min; usaspending = 40/min")
    assert effective_policy("courtlistener") == (
        effective_policy("courtlistener")[0], "EVIDENCE_SEARCH_LIMITS")
    assert effective_policy("courtlistener")[0].windows == [(60.0, 7)]
    assert effective_policy("usaspending")[0].windows == [(60.0, 40)]


def test_the_limiter_enforces_the_override(monkeypatch):
    monkeypatch.setenv("EVIDENCE_SEARCH_LIMITS", TIER3)
    lim = Limiter(Store())
    results = [lim.reserve("courtlistener")[0] for _ in range(21)]
    assert results[:20] == [True] * 20   # the built-in policy stops at 5
    assert results[20] is False


def test_the_limiter_still_stops_at_five_without_one():
    lim = Limiter(Store())
    assert [lim.reserve("courtlistener")[0] for _ in range(6)] == [True] * 5 + [False]


@pytest.mark.parametrize("text", [
    "courtlistner = 20/min",        # misspelt source
    "courtlistener = 20 per min",   # wrong grammar
    "courtlistener = 20/fortnight", # unknown unit
    "courtlistener = 0/min",        # a zero budget is a typo, not a policy
    "courtlistener",                # no windows
    "courtlistener = ",
])
def test_an_unreadable_override_stops_the_tool(text, monkeypatch):
    monkeypatch.setenv("EVIDENCE_SEARCH_LIMITS", text)
    with pytest.raises(LimitsConfigError):
        Limiter(Store()).reserve("courtlistener")


def test_units_and_multiples():
    assert parse_limits("oscn = 8/30min, 3/s, 100/2h, 5/days", "t")["oscn"] == [
        (1.0, 3), (1800.0, 8), (7200.0, 100), (86400.0, 5)]


def test_limits_command_marks_an_override(monkeypatch, capsys):
    from evidence_search.cli import main
    monkeypatch.setenv("EVIDENCE_SEARCH_LIMITS", TIER3)
    assert main(["limits"]) == 0
    out = capsys.readouterr().out
    assert "0/20 per 60s" in out and "OVERRIDE:  windows set by EVIDENCE_SEARCH_LIMITS" in out
