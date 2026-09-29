"""The feedback loop, checked by the suite instead of by memory.

CONTRIBUTING says field reports are the most valuable contribution, and the
project's history backs that up. But the loop ran one-directional for a month:
31 entries accumulated with no triage marker, the same correctness bug was
reported twice three weeks apart because nothing marked the first one known,
and the file's own header went on claiming everything was triaged.

Discipline did not catch that. A test can. These are deliberately loose -- they
do not demand every entry be triaged, only that the backlog stay visible and
the file not lie about itself.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

FEEDBACK = Path(__file__).resolve().parent.parent / "FEEDBACK.md"

#: Entries may sit untriaged; that is normal between passes. This is the point
#: at which "normal backlog" became "nobody is reading this file" -- the state
#: that let one bug be paid for twice. Raise it only with a reason.
_UNTRIAGED_CEILING = 40


def _entries(text: str) -> list[tuple[str, str]]:
    """(heading, body) per dated report. Headings without a date are prose."""
    parts = re.split(r"^## ", text, flags=re.M)[1:]
    out = []
    for p in parts:
        head, _, body = p.partition("\n")
        if re.match(r"\s*\d{4}-\d{2}-\d{2}", head) or re.search(r"\d{4}-\d{2}-\d{2}", head):
            out.append((head.strip(), body))
    return out


def test_feedback_backlog_stays_visible():
    """An untriaged pile past this size means the loop has stopped.

    Not a demand that every entry be marked -- a tripwire for the state where
    workers start re-deriving bugs the file already contains.
    """
    text = FEEDBACK.read_text()
    entries = _entries(text)
    untriaged = [h for h, b in entries
                 if not re.search(r"\[(fixed|wontfix|tracked)", b, re.I)]

    assert len(untriaged) <= _UNTRIAGED_CEILING, (
        f"{len(untriaged)} of {len(entries)} feedback entries carry no "
        f"[fixed]/[wontfix]/[tracked] marker. A log nobody triages is an "
        f"archive, not an engine: the CourtListener soft block was reported "
        f"2026-08-28 with a working root cause, went unmarked, and was "
        f"re-derived from scratch on 09-17. Even [tracked] costs one line and "
        f"saves the next worker the whole re-derivation.\n"
        f"Oldest untriaged: " + "; ".join(untriaged[:3]))


def test_feedback_does_not_claim_a_triage_it_has_not_done():
    """The header once asserted everything below was marked. It was not.

    A reader trusting that line would have concluded 5 items were open when ~31
    entries had never been read. If the claim is made, it has to be dated or
    qualified.
    """
    text = FEEDBACK.read_text()
    claim = "Everything else in this file is marked `[fixed]` or `[wontfix]` inline."
    assert claim not in text, (
        "FEEDBACK.md asserts a complete triage. That sentence was true on "
        "2026-08-19 and false for the month after. Date or qualify it.")
