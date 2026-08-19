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
| `searxng` | none | 30/min | General web via a LOCAL container. 251 engines; coverage rebuilt from its metadata. |
| `usaspending` | none | 30/min | Federal awards. Returns a REAL total by award type — the countable primitive. |
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

cascade-search gate ui        # <- the easy path: worklist in a browser
```

`gate ui` opens a local page (127.0.0.1, stdlib only, no build step) listing
every parked gate as a plain-English description of the records being sought --
"Every Caddo County case filed against a party named Frazier in 2013" -- with
an Open button and a paste target. Solve the challenge, select-all, paste. The
page archives with SHA-256 and drains the queue.

That replaces a five-step ritual, three steps of which were filesystem
bookkeeping rather than work only a human can do: open, solve, save, *find the
file you just saved*, *type its path back into a terminal*.

Two things the page does that the CLI never did:

**It validates the artifact.** `gate resume` archived whatever it was handed,
with a SHA-256, as the record of that search -- so handing back the wrong tab
mid-flow wrote a permanent false record. The page checks the capture against
the gate it claims to answer: it refuses an unpassed challenge page, refuses a
page the party's name never appears in, and *accepts* a "Found No Records" page
because an empty docket is a publishable negative, not a failed capture.

**It collapses duplicates.** The same search can park twice under different
tokens. Solving that Turnstile twice is pure waste, so those cards merge and
one paste clears them all.

The CLI path still works, unchanged:

```bash
cascade-search gate list
cascade-search browser <url> --headed --save /tmp/page.html
cascade-search gate resume 0e96cf371939 --file /tmp/page.html
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

Every reduction is reported honestly on each run (`raw -> extracted, N%`) —
including when there was none. A short source that grows once its provenance is
attached is reported as `EXPANDED by N tok`, not as a negative percentage.

**PDFs**: the embedded text layer is read first — exact, fast, and what court
filings and procurement records actually carry. A *scanned* PDF with no text
layer falls back to OCR, and every such read is labelled:

```
source: OCR (12 pages) -- NOT verbatim, NOT human-verified
```

OCR is lossy in the one way that matters on this beat: it confuses `0`/`O`,
`1`/`l`, `5`/`S` and drops digits in tables, which is exactly the shape of a
docket number or an award ID. Identifiers pulled from OCR'd text carry a
verify-before-citing warning, and the JSON output carries
`provenance.human_verified: false` so nothing downstream mistakes it for
verbatim text. `--no-ocr` refuses instead, if you would rather have a named
blocker than unchecked text.

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

**It is not a general web search replacement** — though `searxng` now closes
most of that gap. It covers the sources this beat uses: courts, contracts,
disclosures, news, docs, and (via a local SearXNG container) the general web.

## General web, on our terms

```bash
deploy/searxng-native.sh up   # no container runtime needed
cascade-search web "query"
```

SearXNG is a Flask app whose dependencies are pure-Python or ship arm64
wheels, so on a Mac with Python 3.10+ it just runs — no VM, no daemon, no
Docker Desktop. `searxng-native.sh` clones it into `deploy/.searxng`, builds a
venv, generates a secret, and starts it on 127.0.0.1. First install takes a few
minutes; after that a cold start is about a second.

Containerised is still there if you want isolation, and prefers the lightest
runtime present — Apple's native `container` (macOS 26+), else podman or
colima, with Docker Desktop last:

```bash
deploy/searxng.sh up
```

SearXNG is 251 maintained engine scrapers behind one JSON API. That catalogue
is the asset. Its *failure semantics* are not, and this client does not adopt
them — two things in its source are disqualifying for a defensible negative:

* `json_engine.py` returns an **empty list** when an engine hits a blocked HTTP
  status, so a wall and an empty shelf are indistinguishable in `results`.
* failures live in a parallel `unresponsive_engines` channel that an operator
  can switch **off** per engine via `display_error_messages`.

So the adapter treats the results array as untrusted on its own and rebuilds
coverage from what the API does report honestly:

```
queried    = engines enabled on the instance   (/config, cached)
failed     = unresponsive_engines              (per search)
responsive = queried - failed
```

A search where 3 of 8 engines were throttled **cannot** certify that a thing
does not exist — it downgrades to `RateLimited`, exactly as a single blocked
source does. A partial sweep is also never cached, since replaying it would
present a degraded search as though every engine had answered.

Scoring stays ours. SearXNG ranks by consensus (`weight * len(positions)`), so
a result four engines agree on scores 4×. On this beat that buries the obscure
trade-press hit or agency subpage only one index carries, so we keep its
per-result `engines` set and let `unique_to_engine` mark those instead.

**`usaspending` is the counting source.** Where `web` cannot count and
`courtlistener` counts filings, this counts federal money:

```bash
cascade-search usaspending "Relentless LLC" --count     # 52 awards, by type
cascade-search usaspending "Relentless LLC" --limit 10  # the awards themselves
cascade-search usaspending "detention" --keywords --count
```

Two search modes, and the difference decides whether the number means anything.
`--recipient` (default) matches the recipient NAME — precise, and the right mode
for a vendor total. `--keywords` is full-text across award descriptions and is
FUZZY: searching "Force Science" returns NURAD Technologies, because the words
appear somewhere in the record. Keyword counts therefore carry an explicit
caveat, because a count of records *containing* words is not a count of awards
*to* anyone.

**`web` is discovery, not census.** Its engines largely ignore quoted phrases,
and SearXNG's engine model declares `paging`, `time_range_support`, `safesearch`
and `language_support` but nothing about exact-phrase support — there is no way
to ask an engine whether it honours quotes. So `web` result sets are fuzzy and
their totals are not countable.

We deliberately do **not** filter locally to compensate. Filtering one page of an
N-page result set produces a number that looks like a count and is not one:
narrowing 30 results to 23 says nothing about the other pages. That is the
page-cap bug in a new place, and a bad count is worse than no count. Instead the
tier says what it is, and a `web` absence is labelled weaker than one from a
corpus with known query semantics.

**Run it locally, not as a service.** The instance binds to 127.0.0.1 by
design. A shared instance means shared rate limits across users — the 200-call
problem one layer up, which the atomic ledger cannot govern across machines.
Datacenter IPs also fare *worse* here: Google and Bing throttle cloud ranges
hardest, so hosting degrades the very engines it exists to reach.

Running it natively sidesteps the runtime question entirely, which is why
that is the default path. Docker Desktop in particular runs a Linux VM plus an
Electron app to proxy search queries — a lot of machine for the job.

## Design notes

**Rate limits are shared state, and reservation is atomic.** Cache, limiter,
and job queue live in one SQLite DB (`~/.cascade-search/store.db`, WAL mode)
because limits belong to the *source*, not the worker. Twelve workers each
assuming they owned the budget is how 200 calls vanished.

Sharing the ledger is necessary but not sufficient: check-then-record is a
TOCTOU race, and N workers reading the same under-limit count all proceed.
`Limiter.reserve()` claims a slot inside one `BEGIN IMMEDIATE` transaction --
insert first, validate after, roll back if over. 200 simultaneous workers
against a 20/s limit are granted exactly 20. Session caps live in the ledger
too, so fan-out cannot bypass them.

`--wait` absorbs sub-second *spacing* waits for sequential shell callers. It
never waits out a budget window: a 50/hr cap must surface as `RateLimited`,
not as a hang.

**Archive on retrieval.** Anything fetched is written to
`documents/sources/files/` with SHA-256 and a manifest line. Never an afterthought.

**Curated results, complete records.** Two failure modes bracket this one.
CourtListener's parser once kept 4 of the 30 fields the API returns, so a worker
counting an industry lost `firm` and `attorney` -- who retained the expert, which
was the entire question -- and fell back to raw HTTP for fields the client had
already fetched and thrown away. But returning all 30 inline is the opposite
error: it spends the caller's context on data nobody asked for, which is what
`extract` exists to prevent.

So every upstream object is persisted verbatim in the `records` table, results
carry a curated view plus a `record_id`, and the rest is one call away:

```bash
cascade-search record --list --source courtlistener
cascade-search record courtlistener:5409345
cascade-search record courtlistener:5409345 --fields party,recap_documents
```

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

Phase 1. **54 tests passing.** Browser escalation, the humanomation gate
(open/list/resume), and local extraction are built and working.

Not yet built: async job-queue consumption, cross-source federated merge
(Phase 3b — ranking one result set across `web`/`news`/`courtlistener`
together), local semantic layer, MCP shim (Phase 2).

Spec: `cascade-research/notes/spec-cascade-search-federated-research-tool.md`
