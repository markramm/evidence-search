"""Exit codes must never let a tool failure impersonate a finding.

The bug these guard against: VerifiedAbsence was exit 1 -- which is also
Python's exit on an uncaught exception, argparse's on a bad flag, and the shell
convention for generic failure. The README branched on `$? -eq 1` and called
that branch "publishable." A crash in a fetch loop would have read as a
certified negative finding.
"""
import subprocess
import sys

import pytest

from cascade_search.cli import (
    EXIT_ACCESS_BLOCKER,
    EXIT_AWAITING_HUMAN,
    EXIT_BY_OUTCOME,
    EXIT_HIT,
    EXIT_INTERNAL_ERROR,
    EXIT_NO_LOCAL_DATA,
    EXIT_RATE_LIMITED,
    EXIT_USAGE,
    EXIT_VERIFIED_ABSENCE,
    cli,
)

OUTCOME_CODES = {
    EXIT_HIT,
    EXIT_VERIFIED_ABSENCE,
    EXIT_ACCESS_BLOCKER,
    EXIT_RATE_LIMITED,
    EXIT_AWAITING_HUMAN,
}
# Every code a failing process can plausibly produce without our say-so.
FAILURE_CODES = {
    1,    # uncaught Python exception; generic shell failure
    2,    # argparse usage error
    126,  # not executable
    127,  # command not found
    130,  # SIGINT
    137,  # SIGKILL
    EXIT_USAGE,
    EXIT_NO_LOCAL_DATA,
    EXIT_INTERNAL_ERROR,
}


def test_no_outcome_code_collides_with_a_failure_code():
    """The core invariant. If these sets ever intersect, a crash can be read
    as a finding by any caller that branches on the exit code."""
    assert OUTCOME_CODES.isdisjoint(FAILURE_CODES)


def test_verified_absence_is_not_exit_one():
    """Named for the specific bug. Exit 1 is what a traceback produces."""
    assert EXIT_VERIFIED_ABSENCE != 1
    assert EXIT_VERIFIED_ABSENCE >= 10


def test_every_outcome_type_has_a_distinct_code():
    codes = list(EXIT_BY_OUTCOME.values())
    assert len(codes) == len(set(codes)), "two outcomes share an exit code"
    assert all(c >= 10 for c in codes), "an outcome code entered the failure range"


def test_uncaught_exception_exits_internal_error_not_an_outcome(monkeypatch):
    """A crash inside main() must surface as EX_SOFTWARE, never as an outcome."""
    def boom(*_a, **_k):
        raise RuntimeError("simulated fetch explosion")

    monkeypatch.setattr("cascade_search.cli.main", boom)
    assert cli() == EXIT_INTERNAL_ERROR
    assert EXIT_INTERNAL_ERROR not in OUTCOME_CODES


def test_keyboard_interrupt_is_not_an_outcome(monkeypatch):
    def interrupt(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr("cascade_search.cli.main", interrupt)
    assert cli() == 130
    assert 130 not in OUTCOME_CODES


def test_bad_flag_is_a_usage_error_not_an_absence():
    """argparse exits 2 on its own; assert that 2 is not a finding."""
    r = subprocess.run(
        [sys.executable, "-m", "cascade_search.cli", "--definitely-not-a-flag"],
        capture_output=True, text=True,
    )
    assert r.returncode not in OUTCOME_CODES, (
        f"a bad flag exited {r.returncode}, which is an OUTCOME code"
    )


def test_empty_local_store_is_not_a_verified_absence():
    """`record --list` with nothing stored is an empty shelf in OUR store.
    It says nothing about the world and must not share absence's code."""
    assert EXIT_NO_LOCAL_DATA != EXIT_VERIFIED_ABSENCE
    assert EXIT_NO_LOCAL_DATA not in OUTCOME_CODES
