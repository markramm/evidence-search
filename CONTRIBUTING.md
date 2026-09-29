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

**Maintainers: triage is not optional bookkeeping.** An untriaged log stops
being an engine and becomes an archive — and the cost is not abstract. The
CourtListener soft block was reported on 2026-08-28 with a correct root cause
and a written spec, went untriaged, and was re-derived from scratch by another
worker on 09-17 who paid the full diagnostic cost again. By then 31 entries had
accumulated with no marker, while the file's own header still claimed
everything was triaged.

Mark entries `[fixed <commit>]`, `[wontfix — reason]`, or `[tracked]`. A
`[tracked]` costs one line and saves the next worker the whole re-derivation.
If you cannot fix it, say it is known.

## The one rule that matters most

**An empty result is never reported as an absence unless it is genuinely one.**

This tool exists because three separate research passes wrote up a *tooling
failure* as a *negative finding*. Every code path that can return
`VerifiedAbsence` must be able to defend it: right corpus, right method, every
engine answered. If any engine was rate-limited or errored, `verified_absence()`
refuses to construct and downgrades to `RateLimited` — do not work around that.

When you add a source, ask what happens when the caller passes a plausible-but-
wrong identifier. If the answer is "we return an absence," that is a bug.

**The guard only catches dirt you declare.** `verified_absence()` refuses on a
coverage that *says* an engine failed. It cannot refuse when a source failed and
built a clean `Coverage` anyway — which is how three false absences shipped with
the suite green: a WAF challenge read as a document, award rows whose amounts
wouldn't parse read as no awards, and a cached absence that dropped its own
caveats. Each one exited 11, which a shell caller reads as publishable.

So: route through `run_source`. Every source that did was correct; the one
function that re-implemented the shell by hand carried the bug. If you must
hand-roll, add your entry point to `ENTRY_POINTS` in
`tests/test_every_source_absence.py` — it sweeps transport failure, rate
limiting and malformed payloads across all sources, and it is what makes the
guarantee cover code nobody thought to spot-check.

Ask specifically: *can this source return zero rows for a reason other than the
corpus being empty?* A parse failure, an unread page, a challenge stub and a
shape change all look like zero. None of them is an absence.

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
