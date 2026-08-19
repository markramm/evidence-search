# cascade-search

Federated research client for the cascade investigation pipeline.

Not a search-engine wrapper. The thing this does that nothing else in the
surveyed landscape does: **it tells you the difference between "we searched
properly and it isn't there" and "the door was shut in our face."**

## Why

On 2026-08-19 a session exhausted its 200-call WebSearch budget across ~12
parallel workers. Meanwhile every load-bearing retrieval that day used **zero**
search calls — OSCN dockets, Montana Cadastral ArcGIS, DOJ's ESAC bulk dataset,
CourtListener, USAspending, Alaska's bulk CSV, Google News RSS.

Worse, three separate research passes recorded a *negative finding* that was
actually a *tooling failure*. Two passes concluded a person had "zero trace"
when they had searched the wrong corpora. A gate-check recorded a KB as thin
because it searched `"watch network"` instead of the movement's own name.

Empty lists lie. Typed outcomes don't.

## Outcomes

Every call returns one of these, never a bare list:

| Outcome | Meaning | Exit |
|---|---|---|
| `Hit` | results found | 0 |
| `VerifiedAbsence` | right corpus, right method, every engine answered — **publishable** | 1 |
| `AccessBlocker` | blocked; **names the mechanism**; flags browser-escalatable | 2 |
| `RateLimited` | tooling-limited, **explicitly not content-exhausted** — retry | 3 |
| `AwaitingHuman` | humanomation gate; automate → human → resume | 4 |

The core guarantee, enforced in code: **if any engine was rate-limited or
errored, a verified absence cannot be constructed.** It downgrades to
`RateLimited` automatically. That is `test_dirty_coverage_cannot_yield_verified_absence`.

## Install

```bash
cd /Users/markr/cascade-search
python3 -m venv .venv && ./.venv/bin/pip install -e .
```

## Use

```bash
cascade-search news '"Desert Snow" interdiction'
cascade-search oscn --county caddo --lname Frazier --year 2013
cascade-search oscn --county caddo --case CF-2013-00038      # archives w/ SHA-256
cascade-search courtlistener "Desert Snow" --type r
cascade-search propublica "Blue Owl"
cascade-search limits                                        # policy + live usage
cascade-search --json news 'query' | jq '.results[].url'
```

Exit codes encode the outcome, so shell callers branch without parsing:

```bash
if cascade-search oscn --county caddo --lname Smith --year 2013 >/dev/null; then
  echo "found"
elif [ $? -eq 1 ]; then
  echo "verified absence — publishable"
fi
```

## Sources

| Source | Auth | Limits | Notes |
|---|---|---|---|
| `news_rss` | none | 20/min | Google News RSS. Worked when WebSearch was dead. |
| `oscn` | none | 8/session, 2s | Turnstile engages ~10 fetches. GET form, `db=<county>`. |
| `courtlistener` | optional token | **5/min · 50/hr · 125/day, concurrent** | `storage.courtlistener.com` serves PDFs where `/recap` 403s. |
| `propublica_disclosures` | none | 15/min | SvelteKit `__data.json`; param is **`q=`** not `search=`. |
| `docs` | none | 25/min | Documentation `llms.txt` indexes. Sites: `claude-code`, `claude-api`. |
| `browser` | none | — | Playwright, **public-records hosts only** (allow-listed). |
| `extract` | none | — | Local dynamic filtering: fields, not pages. |

Set `COURTLISTENER_TOKEN` for the authenticated tier.

## Browser escalation and humanomation

When a source returns `AccessBlocker` with a browser-passable mechanism
(Cloudflare, Turnstile, reCAPTCHA, DataDome, JS-only), the client **escalates to
a real browser automatically**. If the browser also cannot clear the gate, the
job is parked as `AwaitingHuman` with a resume token — not a dead end.

That is the humanomation loop: **automate to the gate, let the human do only
what a human must do, automate again from there.**

```bash
cascade-search oscn --county caddo --lname Frazier --year 2013
#   -> AwaitingHuman, token 0e96cf371939

cascade-search gate list
cascade-search browser <url> --headed --save /tmp/page.html   # solve it yourself
cascade-search gate resume 0e96cf371939 --file /tmp/page.html
#   -> archived with SHA-256, manifest line traced to the gate, queue drains
```

**Ethical boundary, enforced in code.** `core/browser.py` carries an
`ALLOWED_HOSTS` allow-list of court systems and government registries. Anything
else is refused with `host-not-allowed`. This reaches **public records the
agency itself publishes** — never a paywall, an authentication boundary, or
personal data. Adding a host is a deliberate act with a stated reason.

**Headless is detectable.** Bot-detection reliably flags headless Chromium, so
expect `--headed` to be the path that actually clears a live Turnstile. That is
not a defect; it is the humanomation design working as intended.

## Local dynamic filtering — the token lever

The Claude API's `web_search_20260209+` dynamic filtering runs code that filters
results *before* they reach the context window. It is an API-level tool
parameter and is **not exposed as a Claude Code setting**, so we cannot switch it
on for workers. `extract` does the same job locally — and for these sources it
does it better, because we know the schemas.

**Measured baseline** (2026-08-19 pipeline session): 13 workers, **2,317,424
tokens**, 1,168 tool calls — ~1,984 tokens per call. The heaviest workers were
not reasoning-heavy; they were fetch-and-scan loops. Preservation spent 269K
across 140 calls. A worker fetched a page, most of it landed in context, and it
extracted three fields.

```bash
# a worker asking "what happened to Andrea Frazier?"
cascade-search extract case.html \
  --grep "FRAZIER, ANDREA" --grep TRAFFICKING --grep "PAUPER|INDIGENT" --grep DISMISS
# raw ~17,371 tok -> extracted ~841 tok (95.2% reduction)

cascade-search extract https://example.gov/page --ids      # award IDs, dockets, UEIs, statutes
cascade-search extract page.html --tables                  # registry rows, no markup
cascade-search extract page.html --text                    # chrome stripped
```

Every reduction is reported honestly on each run (`raw -> extracted, N%`).

**Identifier extraction is deterministic**, which is why it loses nothing that
matters. Patterns cover federal award IDs, dockets (federal and state), UEI,
CAGE, EIN, money, dates, NAICS/PSC, and statutes (U.S.C., ILCS, O.S.). They were
tightened after a first cut matched `BROADCASTING` as a UEI and `COURT` as a CAGE
code — government identifiers are alphanumeric *mixtures*, so every pattern now
requires at least one digit.

**This composes with subagent isolation rather than replacing it.** Isolation
keeps bulk output out of the *conductor's* context; extraction keeps it out of
the *worker's*. Both are needed.

## What this does NOT do

**It is not a general web search replacement.** It covers the sources this beat
uses — courts, contracts, disclosures, news, docs. Asked an open-ended question,
it will politely return the wrong thing: dogfooding it on a vendor-pricing
question returned product announcements, because news RSS indexes news. That is
why `docs` exists, and why Phase 3 (federated general engines) is still needed.

## Design notes

**Rate limits are shared state.** Cache, limiter, and job queue live in one
SQLite DB (`~/.cascade-search/store.db`, WAL mode) because limits belong to the
*source*, not the worker. Twelve workers each assuming they owned the budget is
how 200 calls vanished.

**Archive on retrieval.** Anything fetched is written to
`documents/sources/files/` with SHA-256 and a manifest line. Never an afterthought.

**Silent-wrong-answer guards.** ProPublica's endpoint returns the *unfiltered*
1,607-row index if you use the wrong parameter name — looking like a broad
success. The client verifies the server echoed the query back and refuses the
results if not.

**URL normalization before dedup.** SearXNG hashes results with no
normalization (its docs say so), so the same page recurs under tracking params.
We strip `utm_*`/`fbclid`/`gclid`, drop `www.`, lowercase the host, normalize
trailing slashes.

**Single-engine finds are surfaced, not buried.** `unique_to_engine` is marked
in output. On this beat, the result only one index found is often the valuable
one — obscure trade press, an agency subpage, an old docket.

**`cacheable=False` where terms forbid storing results.** Brave's standard plans
do not grant storage rights; that is a policy field, not a footnote.

## Status

Phase 1. 12 tests passing. Not yet built: browser escalation (Playwright +
FlareSolverr), async job queue consumption, humanomation gate resume,
federated multi-engine merge, local semantic layer.

Spec: `cascade-research/notes/spec-cascade-search-federated-research-tool.md`
