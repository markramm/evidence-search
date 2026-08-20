# Contributing

## The most valuable contribution is a field report

`FEEDBACK.md` is a running log of friction found by agents and people using the
tool on real research tasks. Most of the bug fixes in this repo's history came
from it rather than from synthetic tests — including several false-negative bugs
that no unit test would have caught, because they only appear when someone is
mid-task and reaches for the identifier they happen to have.

A good report names **the task you were doing** when the tool failed you, not
just the symptom. "I had a PIID off a contract and `--detail` told me the award
didn't exist" is worth more than "404 handling is wrong."

Append to `FEEDBACK.md`; never edit someone else's entry.

## The one rule that matters most

**An empty result is never reported as an absence unless it is genuinely one.**

This tool exists because three separate research passes wrote up a *tooling
failure* as a *negative finding*. Every code path that can return
`VerifiedAbsence` must be able to defend it: right corpus, right method, every
engine answered. If any engine was rate-limited or errored, `verified_absence()`
refuses to construct and downgrades to `RateLimited` — do not work around that.

When you add a source, ask what happens when the caller passes a plausible-but-
wrong identifier. If the answer is "we return an absence," that is a bug.

## Tests

```bash
python3 -m venv .venv && ./.venv/bin/pip install -e '.[test]'
./.venv/bin/python -m pytest -q
```

New sources need coverage of the blocked and rate-limited paths, not only the
happy path. Regression tests for false-negative bugs should say in the docstring
*why* the negative was wrong — the reasoning is the durable part.

## The browser tier is an ethical boundary

`ALLOWED_HOSTS` in `core/browser.py` is public records only: court systems and
government registries. Never a paywall, never an authentication boundary, never
personal data. Adding a host is a deliberate act and needs a reason in the PR.

## Style

Match the surrounding code. Comments explain *why*, especially where a line
encodes a lesson learned in the field — those comments are load-bearing and
should not be trimmed as noise.
