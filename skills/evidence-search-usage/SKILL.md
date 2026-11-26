---
name: evidence-search-usage
description: Use evidence-search — the federated research client with typed outcomes (Hit / VerifiedAbsence / AccessBlocker / RateLimited / AwaitingHuman) over ~30 sources: court dockets, federal awards and contract opportunities (USAspending, SAM.gov), Federal Register, SEC, FEC, lobbying, FARA, 990s, sanctions, regulators (EPA, FDA, CFPB), data-broker registries, news and general web — and log usability friction back to its maintainers. Use it whenever investigation work needs a court record, an award amount or ID, a filing, or a DEFENSIBLE NEGATIVE ("we searched properly and it isn't there") rather than an empty list; whenever a page must be read to quote or cite it (extract returns the page's own words, WebFetch a paraphrase); when a search hits a 403, CAPTCHA or Cloudflare wall; or when the user says "use evidence-search", "fetch this with evidence-search", "search with the new tool", "log an evidence-search issue", or "/evidence-search-usage".
---

# evidence-search-usage

Operate `evidence-search`, and report back on how it went.

Two jobs, and **the second is not optional**. Your account of where the tool
confused you is the primary evidence for what gets fixed. See *Report the
friction* below.

```bash
CS=evidence-search          # if installed on PATH (pip install -e .)
# or, from a venv checkout:
CS=/path/to/evidence-search/.venv/bin/evidence-search
```

## Why this instead of WebSearch

An empty list cannot tell you whether you searched properly and found nothing,
or whether the door was shut in your face. Three separate research passes wrote
up a *tooling failure* as a *negative finding* before this existed.

Every call returns one typed outcome, and the exit code carries it:

| Outcome | Exit | What you may write |
|---|---|---|
| `Hit` | 10 | the results |
| `VerifiedAbsence` | 11 | **a publishable negative** — right corpus, right method, every engine answered |
| `AccessBlocker` | 12 | **not a negative finding.** The mechanism is named (Turnstile, Cloudflare, 403, JS-only) |
| `RateLimited` | 13 | **tooling-limited, not content-exhausted.** Retry later; never write it up as absence |
| `AwaitingHuman` | 14 | a human gate was queued with a resume token |

Anything **below 10 is a tool failure**, not a claim about the world: `1`
uncaught exception, `2` usage error, `3` no local data, `70` internal error.

The guarantee is enforced in code: if any engine was rate-limited or errored,
a verified absence **cannot** be constructed — it downgrades to `RateLimited`.
So `VerifiedAbsence` is the one negative you may report as a finding.

**But read the `ASKED:` block before you write the absence up.** Typed outcomes
prove the search was *executed* cleanly. They prove nothing about whether it was
*specified* correctly — the tool will happily certify a flawless search for the
wrong string. An absence on `Frazier` is an absence of that string: not
`Frasier`, not a married name, not a hyphenation, not a data-entry variant. In
`--json` the same thing is at the top level as `asked`.

When a name could vary, ask the variants before you claim the absence. When the
`ASKED` line says `FUZZY`, the endpoint could not honour exact-phrase matching
and the negative is weaker than it looks.

**Outcome exit codes are 10+; every failure code is below it.** Exit 1 means the
tool broke — never that the world is empty.

```bash
$CS oscn --county tulsa --lname Smith --year 2013 >/dev/null
case $? in
  10) echo "found" ;;
  11) echo "verified absence — publishable as scoped" ;;
  12) echo "BLOCKED — record the mechanism, claim nothing" ;;
  13) echo "rate-limited — retry; NOT content-exhausted" ;;
  14) echo "human gate queued" ;;
   *) echo "tool failure — say nothing about the world" ;;
esac
```

## Commands

```bash
$CS --wait web "warehouse detention contract"      # zero-daemon web (Mwmbl default; optional API key or SearXNG)
$CS web "ICE detention contract" --provider brave --freshness pw   # Brave only: past day/week/month/year (pd|pw|pm|py) or YYYY-MM-DDtoYYYY-MM-DD
$CS web "detention audit" --provider brave --goggle ~/.config/brave/tcp-sources.goggle  # Brave only: re-rank by a goggle (URL, inline rules or file; up to 3)
$CS --wait news '"Boeing" 737 inspection'           # Google News RSS, keyless
$CS courtlistener '"Boeing"' --type r              # federal + state dockets
$CS courtlistener --docket dcd:1:26-cv-01352       # watch a specific RECAP docket (Last Updated, deadlines, entries)
$CS usaspending "Booz Allen Hamilton" --count      # REAL federal award total
$CS usaspending "Booz Allen Hamilton" --limit 10   # the awards themselves
$CS usaspending "GEO Group" --agency ICE --sum     # filter by awarding/funding agency (name or alias: ICE, CBP, DHS, DOD, DOJ)
$CS usaspending --idv 70CDCR20D00000011            # child task/delivery orders under an IDV/BPA umbrella
$CS usaspending --transactions CONT_AWD_...        # modification timeline (P00001..N, action dates, ceiling changes)
$CS samgov "detention" --active-only               # SAM.gov pre-award solicitations, special notices, sources sought
$CS samgov --detail <opportunity_id>               # full solicitation notice description + points of contact
$CS samgov --attachments <opportunity_id>          # downloadable attachment URLs for `extract`
$CS lda --client "Purdue Pharma" --year 2008       # Senate LD-1/LD-2 federal lobbying filings (1999-present)
$CS fec --committee "Purdue" --cycles 2006,2008    # bulk-first FEC committee ID lookup
$CS fec --from-committee C00370643 --to-committee C00367862 --cycles 2006-2008  # bulk-first FEC PAC transfers
$CS nonprofit "Donors Trust"                       # IRS Form 990 index (warns on multi-EIN collisions)
$CS nonprofit --ein 52-2166327 --year 2024         # 990 filing history + PDF URLs for a single EIN
$CS sec '"CoreCivic"' --forms 10-K,10-Q,8-K        # SEC EDGAR EFTS full-text search (2001-present)
$CS sec --company CXW --forms 8-K                  # SEC company submission history by CIK (--cik alias), ticker, or name
$CS sanctions "Intellexa"                          # OFAC SDN + EU Consolidated sanctions (exact_match vs possible_match)
$CS fara --country "El Salvador"                   # DOJ FARA foreign principals & U.S. registrants
$CS wayback "https://vendor.com/page" --collapse-digest  # Wayback Machine CDX captures + SHA-1 content digests
$CS openalex --author "John Doe" --retracted-only  # OpenAlex scholarly works, grants, and Retraction Watch flags
$CS socrata "Vendor LLC" --domain data.cityofnewyork.us --dataset abcd-1234  # any Socrata state/city portal
$CS arcgis "Holdings LLC" --layer-url https://.../FeatureServer/0 --field OwnerName  # any Esri ArcGIS parcel/registry layer
$CS montana_cadastral "Holdings LLC"                # shipped Montana parcel ArcGIS plugin (top-level subcommand)
$CS datagov_catalog "detention contracts"          # shipped Data.gov REST v1 dataset catalog plugin
$CS kev "Ivanti" --ransomware-only                 # CISA KEV mandatory-patch list + NIST NVD CVE 2.0
$CS crtsh "%.contractor.gov" --active-only        # Certificate Transparency subdomain & shared-SAN discovery
$CS epa "Exxon" --state TX --violators-only        # EPA ECHO facilities, non-compliance quarters, penalties
$CS openfda "infusion pump" --endpoint recall      # OpenFDA device recalls, 510(k) clearances, enforcement
$CS cfpb "fees" --company "SECURUS TECHNOLOGIES"   # CFPB consumer complaints & narratives
$CS icij "Holdings" --type entity                  # ICIJ Offshore Leaks entities, officers & 1-hop links
$CS brokers "LiveRamp" --state CA                  # CA, VT, TX, OR state data-broker registries
$CS gdelt "border surveillance" --country US       # GDELT DOC 2.0 global news discovery (uncountable)
$CS investigate "CoreCivic" --domain corecivic.com # coordinated sweep + automatic CIK/EIN/UEI/domain pivots
$CS mcp --list-tools                               # Phase 2 MCP stdio server & tool inspector (`evidence-search-mcp`)
$CS plugin check [--live]                          # 6-point absence-safety conformance suite (+ live canary)
$CS plugin init my_portal --driver socrata --domain data.example.gov --dataset abcd-1234
$CS plugin init my_rest --driver rest --endpoint https://data.example.gov/api/search --rows-path results
$CS oscn --county tulsa --lname Smith --year 2013
$CS oscn --county tulsa --case CF-2013-00001       # one case, archived w/ SHA-256
$CS propublica "BlackRock"                         # Trump-team disclosures
$CS fedreg "immigration detention" --type rule     # rules/notices/EOs, real count
$CS crossref 10.1177/10986111251357498             # exact scholarly record
$CS docs "prompt caching" --site claude-api        # vendor documentation
$CS sources                                        # machine-readable catalog of every source's tier, counts, and caveats
$CS limits                                         # usage, 429 blocks, backoff: WHY a call was refused
$CS gate ui                                        # human gates, in a browser
$CS gate cancel <token> --reason "test fixture"    # dismiss a stale or test-run human gate
$CS record <result-id>                             # the FULL upstream record behind a result
$CS hosts                                          # the browser-tier allow-list (public-records hosts only)
$CS browser <url> [--headed] [--save page.html]    # real-browser fetch, allow-listed public-records hosts only
$CS --json web "query" | jq '.results[].url'
```

**Use `--wait` for sequential calls.** Sources carry a 0.5–2s minimum spacing;
without it, two back-to-back commands return `RateLimited` on the second.
`--wait` absorbs only that spacing, never a real budget window.

**Use `--wait-max SECONDS` when other agents are working the same source.** It
queues for a slot (spacing, the per-minute window, a shared 429 block, a
backoff) for up to that long instead of returning `RateLimited`, and wakes at a
slightly different moment from the other waiters. It never waits out an hour or
day window or a session cap: those return `RateLimited` at once with the time
the window reopens. On `RateLimited`, do not loop on the command — run
`$CS limits` and read `next call:` for that source. If it names a 429 block or
a long window, the wait is the number shown; move to other work.

**`extract` and `browser` are rate-limited too.** Each fetch claims a slot:
OSCN pages count toward OSCN's session cap (8 per 30 minutes, shared by every
agent), other source hosts toward that source, and any other host is held to
one request a second and 30 a minute across all agents. They wait up to 10
seconds for a slot on their own and then exit 13. Local files are unaffected.

**`--json`, `--wait` and `--wait-max` are global** — they work in either position, before or
after the subcommand. **`--limit` works on every listing source**, spelled the
same way everywhere (`--rows` and `--per-page` still work where they always
did). Trimming the list never hides the total: the header keeps reporting it.

**`web` works zero-daemon out of the box.** By default it queries the keyless
Mwmbl JSON API in process (and automatically uses `BRAVE_API_KEY`,
`SERPER_API_KEY`, `TAVILY_API_KEY`, or an optional `SEARXNG_URL` / `--base`
when configured, or `--provider <name>`). Unlike external `WebSearch`, `web`
records structured coverage and probes — never record an external `WebSearch`
result as equivalent coverage.

**That is not a reason to avoid `WebSearch`.** It is a first-class tool for a
different job: exploratory questions, first leads, and citable news links (this
tool's `news` returns Google redirects that cannot be cited). A worker reported
"~11 evidence-search calls, no WebSearch fallback" as if that were the goal. It is
not — use whichever finds the thing, and be precise about which one supports a
claim of absence.

**`web` is a discovery tier, not a census.** Its upstream engines largely ignore
quoted phrases, and SearXNG exposes no capability flag that would tell us which
ones honour them — so a `web` result set is fuzzy and its totals are not
countable. Do not count with it, and treat a `web` zero as a *weak* negative;
the outcome will say so.

When a count or a defensible absence is the deliverable, use a source whose
corpus and query semantics are known. `courtlistener` reports a real
`total_matches` for a quoted phrase.

**Know which sources can count.** Getting this wrong produces a confident number
that is wrong, which is worse than no number:

| Source | Counts? |
|---|---|
| `usaspending`, `fedreg`, `lda`, `fec`, `samgov`, `nonprofit` | **yes** — defined corpora, real totals |
| `courtlistener`, `sec` | **yes**, if you quote the phrase — read `total_matches` |
| `sanctions` | **yes**, on normalized aliases (`--exact`); fuzzy hits are flagged `possible_match` |
| `crossref`, `web` | **no** — both match loosely; their totals measure nothing |

**Per-source traps live in `references/sources.md`.** Read the section for your
source before citing a total or a record from it: USAspending (PSC/NAICS, `--sum`,
shared award IDs across parent and subsidiary, UEIs on assistance awards,
`--count` vs `--keywords`, IDVs and modification timelines), CourtListener
(attorney-of-record matches, `--docket` watches), Federal Register ranking,
SAM.gov, LDA/FEC, nonprofit 990 name collisions, SEC EDGAR, sanctions, and
Crossref for paywalled articles.

**Counting filings? Quote the phrase, and read the total off the header.** A
source that knows its true total now prints it directly:
`results:  20 of 293 total_matches  (this page only -- cite 293, not 20)`.
Cite that number, not the row count — "at least 20" is almost never the honest
answer. (In `--json`, `total_matches` and `asked` are both exposed at the top
level of `Hit` as well as on `results[0].meta.total_matches`.) And CourtListener
tokenises unquoted queries: a real three-word institute name reported 51,622
matches unquoted and 155 quoted. Quote it, or your count is off by two
orders of magnitude in the wrong direction.


## Read coverage before you trust a result

Every outcome carries a coverage line. Read it:

```
coverage: 80/82 responsive | RATE-LIMITED: brave | ERRORED: startpage
```

That is a *good* result with two known gaps. A zero-result search with the same
line is **not** an absence — it is a partial sweep, and the tool will have
downgraded it to `RateLimited` for exactly that reason.

`*UNIQUE*` on a result means one engine found it. On this beat that is often
the valuable one — obscure trade press, an agency subpage, an old docket.
Consensus ranking buries those; this surfaces them.

## extract — read pages cheaply

The biggest token lever available to you, and it loses nothing: extraction is
deterministic regex/DOM traversal, **not model summarisation**.

```bash
$CS extract <url-or-file> --grep "DEFENDANT" --grep "DISMISS|JUDGMENT"
$CS extract <url-or-file> --ids      # award IDs, dockets, UEI, CAGE, EIN, money, statutes
$CS extract <url-or-file> --tables   # registry rows, no markup
$CS extract <url-or-file> --text     # chrome stripped, for open-ended reading
```

Measured on a live article: **96,655 → 5,724 tokens (94.1%)**, and `--ids`
pulled the exact dollar figures in 27 tokens. Every run reports its reduction.

**To quote or cite a page, read it with `extract` and copy the words from its
output.** `extract --text` and `--grep` return the page's own sentences.
WebFetch returns a model's paraphrase of the page: fine for orientation, never a
quotation, a span or a figure you cite. A worker who checked ten claims through
WebFetch summaries came back with nothing citable, and a second worker had to
re-read every source with `extract`.

**Output is capped for display at about 4,000 characters.** A long page ends with
`[TRUNCATED for display: ... Use --json for the full text, or --max-chars N]`.
Raise `--max-chars` or use `--json` rather than assuming the page ended there.

**PDFs are handled.** `extract` reads the embedded text layer — exact and fast,
which is what court filings, procurement records, and the Federal Register all
carry. If a PDF is *scanned* and has no text layer, it falls back to OCR and
says so loudly:

```
source: OCR (12 pages) -- NOT verbatim, NOT human-verified
```

Treat OCR'd text as a lead, not a citation. It confuses `0`/`O`, `1`/`l`, `5`/`S`
and drops digits in tables — precisely the characters in docket numbers, award
IDs, and dollar figures. **Verify every identifier against the page image before
citing it**, and say in your artifact that the figure came from OCR. Use
`--no-ocr` if you would rather be told the document is unreadable than be handed
text nobody has checked.

## When you hit a wall

`AccessBlocker` names the mechanism (403, Cloudflare, Turnstile, DataDome,
JS-only, paywall). It may also print `ESCALATABLE: a real browser session could
plausibly pass this gate.` That line is information, not an instruction.

**The browser tier is for public-records hosts on the allow-list, nothing
else.** `$CS hosts` lists them (state courts, SAM, SBA, county property and
business registries): public records whose sites block scripts for load reasons.
Use `extract --browser` or `browser` there. On any other site, a wall is the
publisher declining automated access: record it as access-blocked with the
mechanism, stop on that source, and do not retry it with another tool, a
browser, a different user agent or an archive copy fetched to get around it. If
the page matters, hand the URL to a human to read and paste (that is how
HPCwire and an Argonne page reached the record on 2026-11-25). A 429 may be
retried once after the window `limits` names.

When an allow-listed host's browser fetch also fails, the job parks as
`AwaitingHuman` with a resume token. Record the token in your work log and move
on; gates are cleared in batches via `$CS gate ui`. A gate is never an absence.

**A 404 is not a wall.** It prints `NOT a negative finding, and NOT a block —
the URL does not exist`, and it means you guessed the path wrong (`/about` vs
`/about-us`) or, when sweeping a sequential id range, that the id was never
issued or has been pruned. Do not read it as suppression: two workers nearly
wrote that a publication was blocking its own about-page. Check the path and
retry. The exit code is still 12 (AccessBlocker) — a 404 is never a verified absence.

## Report the friction

**This is the second half of the task.** evidence-search is under active
evaluation; your account of where it confused you is the primary evidence for
what gets fixed next. Size it to what happened: when nothing surprised you, one
line in `FEEDBACK.local.md` (date, task kind, sources used, "no friction") is
enough. When something did, use the full method below.

Invoke the `hallway-agent-testing` skill — it carries the method (what counts as
friction, how to write it so it is actionable, how to report a command
faithfully).

**Where it goes — two files, because the repo is public.** Write the raw entry
to `FEEDBACK.local.md` in the evidence-search checkout. It is gitignored, so it
can name the task, the subject, the ticket and the local path — whatever makes
the report useful to you and the maintainer. Nothing in it is ever published.

`FEEDBACK.md` (tracked, public) and GitHub issues take **redacted** entries
only. Before copying anything there, replace people, investigations, vendors,
committees and places with stable placeholders (`<person-a>`, `<vendor-a>`,
`<pac-a>`, `<county-a>`), describe the task by its kind ("a federal-court
docket watch"), and remove local paths, KB ids, ticket and draft names. Public
identifiers that ARE the bug — a PIID, an API endpoint, a public government
domain — stay. For a "False negative" or "New source request" issue, use the
templates, redacted the same way.

The short version: report anything you had to look up or guess, anything
surprising, every error verbatim with its command, **where the results
themselves fell short** (wrong corpus, missing source, ranking that buried the
answer, a number you could not trust), and what actually worked. Report it even
if you worked around it — especially then.

Two cautions specific to this tool:

- **Copy the command, do not reconstruct it.** Two workers independently
  reported "extract exits 0 on AccessBlocker." It exits 12. They had piped to
  `head`, so `$?` was `head`'s. Separate what you *observed* from what you
  *concluded*.
- **Missing sources are findings.** Several real gaps surfaced this way
  (USAspending, Federal Register, Crossref). "This beat needs X and the tool has
  no client for it" is as useful as a bug.

## What it does not do

It is not a general web search replacement for *every* question — `web` covers
much of that gap, but the structured sources (courts, contracts, disclosures)
are where it is strongest. Asked something open-ended and off-beat, it may
politely return the wrong thing. Use `WebSearch` then, and **log that you had
to**.

**Publication-coverage counts are out of scope. Do not construct one.** "How
much does site X write about entity Y" has no honest path here, and both
available roads produce a number that looks like a census and is not one.
Site-scoped engine counts are estimates that move between runs. A publication's
own search tokenises: on one trade site, a two-word vendor name returned 1,967
hits and another 28,081 — those count the individual WORDS, not the phrases,
the same error class as an unquoted CourtListener query. A number produced that
way looks like a census and is not one. Answer media-ownership and coverage-bias
questions from ownership records and named examples, and report reach as
**not determinable** rather than citing a site-search figure.
