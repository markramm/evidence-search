# Design note: a source-plugin system for evidence-search

Status: proposal, not built. Written 2026-08-20 after Mark raised the idea.

## The problem, measured

Adding one source today touches **five places**, three of which are edits to
existing shared files rather than new files:

1. `sources/<name>.py` — the client (new file, fine)
2. `cli.py` — an argparse subparser (14 exist today)
3. `cli.py` — a dispatch branch in `main()` (14 exist)
4. `core/limits.py` — a `POLICIES` entry (15 exist)
5. `README.md` + `SKILL.md` — documentation, by hand

There is **no registry**: `grep` for `SOURCES =`, `REGISTRY`, or `entry_points`
returns nothing. `main()` carries nine hardcoded `from .sources import ...`
lines inside its dispatch. `main()` is 537 lines.

The consequence is that a contributor cannot add a source without editing the
CLI, and the CLI cannot enumerate its own sources.

## The finding that makes this cheap

**The self-documentation already exists.** Every migrated source already declares
its own epistemic limits — they are just passed as loose kwargs to `run_source`
instead of being declared as an inspectable manifest:

| field | what it carries | e.g. crossref |
|---|---|---|
| `corpus` | what this endpoint actually covers | "Crossref works index (~150M records), matched LOOSELY on title. Totals are not measurements." |
| `not_searched` | the honest boundary of a negative | "works with no Crossref DOI registered", "full text -- Crossref indexes metadata only" |
| `caveats` | why a negative here might still be wrong | "FUZZY title search ... a differently-worded title would not be found. Prefer a DOI lookup where one exists." |
| `exact_match_supported` | whether a negative can ever be absolute | False |

Coverage as of today: courtlistener, crossref, federal_register, oscn, usaspending
all declare `corpus`/`not_searched`; docs and propublica_disclosures do not.

That is a better self-description than most MCP tool definitions, because it
describes *what the tool cannot tell you*, not just what it accepts. It is
currently unreachable — nothing can enumerate it without running a search.

## Proposed shape

A dataclass manifest per source, plus a registry. Deliberately NOT a new
abstraction layer — `run_source` already IS the plugin runtime.

```python
@dataclass(frozen=True)
class SourceSpec:
    name: str                      # "crossref" -- CLI subcommand AND limiter key
    summary: str                   # one line for `evidence-search sources`
    corpus: str                    # what it covers
    not_searched: list[str]        # boundary of any negative
    caveats: list[str]             # why a negative might be wrong
    exact_match_supported: bool | None
    policy: Policy                 # rate limits travel WITH the source
    args: list[Arg]                # declarative -> argparse
    run: Callable                  # (ns) -> Outcome
    tier: int = 2                  # source-quality tier
    requires_key: str | None = None
```

Then:
- `cli.py` builds subparsers by iterating the registry — the 14 hand-written
  subparsers and 14 dispatch branches collapse into one loop
- `POLICIES` is derived from the registry rather than maintained separately, so
  the rate policy cannot drift from the source that needs it
- `evidence-search sources` prints the manifest — self-documenting, MCP-style
- `evidence-search sources --json` gives an agent the whole catalog in one call,
  including what each source CANNOT answer

## Why this is worth doing beyond ergonomics

**It closes the backoff-gap class of bug.** The `note_outcome` defect found
2026-08-20 existed because `reserve()` consulted a counter that four of nine
sources never fed — partial adoption of a shared mechanism, invisible because
nothing could enumerate the sources. A registry makes "which sources implement
X?" a one-liner instead of an audit.

**It makes the epistemics discoverable.** Right now an agent must already know
that `crossref` title search is fuzzy. With `sources --json` it can read that
before choosing a source — which is the entire premise of typed outcomes,
extended one level up.

## Scope discipline

- Do NOT build entry-point/third-party plugin loading first. In-tree registry
  only. External plugins are a distribution problem, and nobody is asking to
  distribute one yet.
- `searxng` will not fit the uniform `run` signature (per-upstream-engine
  coverage; see the 2026-08-20 decision not to migrate it to `run_source`).
  The registry must tolerate a source that supplies its own runner.
- `usaspending` has four entry points (`counts`, `detail`, `dollar_sum`,
  `search`), so `run` cannot assume one verb per source. Either sub-verbs in
  the spec, or one spec per verb.
- The manifest must not become a place to assert coverage we have not verified.
  `corpus` and `not_searched` are load-bearing for publishable negatives; a
  wrong `not_searched` produces an overclaimed absence, which is the exact
  failure this tool exists to prevent.

## Suggested first step

Convert two sources (one simple: `federal_register`; one awkward: `usaspending`)
and leave the other seven on the current path. If the spec cannot express the
awkward one without contortion, the design is wrong and better to learn it at
two than at nine.
