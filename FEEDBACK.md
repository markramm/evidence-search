# cascade-search — field feedback

Hallway usability testing, with agents as the users.

Agents doing real investigation work log friction here: what confused them, what
they had to look up, where the results fell short, and what they worked around.
A silent workaround is the most expensive failure mode — it hides a defect *and*
costs the worker the time they spent routing around it.

**Append; never edit someone else's entry.** Rough notes beat polished ones.
Precise complaints beat praise. A clean run is a data point too — say so in one
line.

## Format

```markdown
## YYYY-MM-DD · <task-id or context> · <model>
**Command:** the exact command
**Expected:** what you thought would happen
**Got:** what happened
**Friction:** where you got stuck, or what you had to work around
**Had to figure out:** anything you looked up, guessed, or discovered by trial
**Would have helped:** the change that would have saved you
**Severity:** blocked | slowed | annoyed | cosmetic
```

Skip fields that do not apply. Add `**Worked well:**` when something carried its
weight — knowing which parts earn their keep is how the rest gets prioritised.

## Triage

Maintainers: mark entries `[fixed <commit>]`, `[wontfix — reason]`, or
`[tracked]`. Leave the original text intact so the pattern stays visible.

---

## 2026-08-19 · build session · claude-opus-5 · SEED ENTRIES

Found while building and dogfooding. Recorded here as worked examples of the
format, and because the failure modes are the ones worth watching for.

### ProPublica returned 35 identical placeholder rows

**Command:** `cascade-search propublica "Blue Owl"`
**Expected:** appointee names and disclosure detail
**Got:** 35 rows, every one titled `(unnamed appointee)`, all sharing one URL
**Friction:** looked like a broad successful hit. Nothing in the output said the
data was wrong — this is exactly the silent-wrong-answer class the tool exists
to prevent, appearing inside the tool.
**Had to figure out:** that `--no-cache` returned correct data, which localised
it to a poisoned cache entry rather than the parser.
**Would have helped:** the tool refusing to cache a payload where every row
shares one placeholder identity; and cached results disclosing their age.
**Severity:** blocked
`[fixed f7f2f61 — Store.put raises CachePoisoned; replayed results now carry cache_age_s]`

### Two back-to-back commands, second one RateLimited

**Command:** any two sequential calls to the same source
**Expected:** both to run
**Got:** `RateLimited` on the second, with a 0.5s wait
**Friction:** the README's own examples are sequential, so following the docs
produced an error.
**Would have helped:** an opt-in flag to absorb short spacing waits.
**Severity:** annoyed
`[fixed 763062c — --wait absorbs spacing only, never a budget window]`

### Could not tell whether a gate was worth solving twice

**Command:** `cascade-search gate list`
**Expected:** a list of distinct things needing a human
**Got:** the same OSCN search parked twice under different tokens, plus two
entries showing `gate_type: None` / `capture: None`
**Friction:** no way to see the two were the same target without reading URLs
closely; "None" where a description belonged.
**Would have helped:** collapsing duplicates, and describing gates in English.
**Severity:** slowed
`[fixed 6801ad2 — gate ui dedupes and describes; CLI list unchanged]`

### SearXNG failures misclassified under a non-English locale

**Command:** `cascade-search web "..."` against an instance answering in German
**Expected:** throttled engines reported as rate-limited
**Got:** `zu viele Anfragen` matched nothing and was filed as a hard error
**Friction:** failed safe (coverage still dirty, no false absence) but with the
wrong label — and a wrong label on a retryable condition is how a worker gives
up on something it should have retried.
**Would have helped:** pinning the response locale.
**Severity:** slowed
`[fixed 7533d97 — locale=en pinned; unrecognised messages now flagged unclassified]`

---
