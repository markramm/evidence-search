---
name: cascade-search-usage
description: Use cascade-search — the federated research client with typed outcomes (Hit / VerifiedAbsence / AccessBlocker / RateLimited / AwaitingHuman) covering courts, contracts, disclosures, news, vendor docs, and the general web via a local SearXNG instance — and log usability friction back to its maintainers. Use when searching court dockets, federal cases, financial disclosures, news, or the general web during investigation work; when you need a DEFENSIBLE NEGATIVE ("we searched properly and it isn't there") rather than an empty list; when a page must be read cheaply instead of pulled whole into context; when a search hits a CAPTCHA or Cloudflare wall; or when the user says "use cascade-search", "search with the new tool", "log a cascade-search issue", or "/cascade-search-usage".
---

# cascade-search-usage

Operate `cascade-search`, and report back on how it went.

Two jobs, and **the second is not optional**. Your account of where the tool
confused you is the primary evidence for what gets fixed. See *Report the
friction* below.

```bash
CS=cascade-search          # if installed on PATH (pip install -e .)
# or, from a venv checkout:
CS=/path/to/cascade-search/.venv/bin/cascade-search
```

## Why this instead of WebSearch

An empty list cannot tell you whether you searched properly and found nothing,
or whether the door was shut in your face. Three separate research passes wrote
up a *tooling failure* as a *negative finding* before this existed.

Every call returns one typed outcome, and the exit code carries it:

| Outcome | Exit | What you may write |
|---|---|---|
| `Hit` | 0 | the results |
| `VerifiedAbsence` | 1 | **a publishable negative** — right corpus, right method, every engine answered |
| `AccessBlocker` | 2 | **not a negative finding.** The mechanism is named (Turnstile, Cloudflare, 403, JS-only) |
| `RateLimited` | 3 | **tooling-limited, not content-exhausted.** Retry later; never write it up as absence |
| `AwaitingHuman` | 4 | a human gate was queued with a resume token |

The guarantee is enforced in code: if any engine was rate-limited or errored,
a verified absence **cannot** be constructed — it downgrades to `RateLimited`.
So `VerifiedAbsence` is the one negative you may report as a finding.

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
$CS --wait web "warehouse detention contract"      # general web (local SearXNG)
$CS --wait news '"Boeing" 737 inspection'           # Google News RSS, keyless
$CS courtlistener '"Boeing"' --type r              # federal + state dockets
$CS usaspending "Booz Allen Hamilton" --count      # REAL federal award total
$CS usaspending "Booz Allen Hamilton" --limit 10   # the awards themselves
$CS oscn --county tulsa --lname Smith --year 2013
$CS oscn --county tulsa --case CF-2013-00001       # one case, archived w/ SHA-256
$CS propublica "BlackRock"                         # Trump-team disclosures
$CS fedreg "immigration detention" --type rule     # rules/notices/EOs, real count
$CS crossref 10.1177/10986111251357498             # exact scholarly record
$CS docs "prompt caching" --site claude-api        # vendor documentation
$CS limits                                         # policy + live usage
$CS gate ui                                        # human gates, in a browser
$CS --json web "query" | jq '.results[].url'
```

**Use `--wait` for sequential calls.** Sources carry a 0.5–2s minimum spacing;
without it, two back-to-back commands return `RateLimited` on the second.
`--wait` absorbs only that spacing, never a real budget window.

**`--json` and `--wait` are global** — they work in either position, before or
after the subcommand. **`--limit` works on every listing source**, spelled the
same way everywhere (`--rows` and `--per-page` still work where they always
did). Trimming the list never hides the total: the header keeps reporting it.

**`web` needs a local instance.** If it reports no instance:

```bash
./deploy/searxng-native.sh up      # from the repo; no container runtime needed
```

Start it and retry. The one thing not to do is record a `WebSearch` result as
*equivalent coverage* — `web` reports which engines answered and `WebSearch` does
not, so only `web` can support a coverage claim.

**That is not a reason to avoid `WebSearch`.** It is a first-class tool for a
different job: exploratory questions, first leads, and citable news links (this
tool's `news` returns Google redirects that cannot be cited). A worker reported
"~11 cascade-search calls, no WebSearch fallback" as if that were the goal. It is
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
| `usaspending`, `fedreg` | **yes** — defined corpora, real totals |
| `courtlistener` | **yes**, if you quote the phrase — read `total_matches` |
| `crossref`, `web` | **no** — both match loosely; their totals measure nothing |

**Hit a paywall on a journal article?** `crossref <doi>` returns the
authoritative record — exact title, journal, year, every author — which is
usually what a citation needs even when the full text is walled. A DOI lookup is
exact; a *title* search is fuzzy and carries a verify-before-citing warning
(probing "Forced Science" returns "Forced to Pursue Science" on top).

**Need the PSC/NAICS on a federal award?** `usaspending --detail <record_id>` returns
them; the search results carry only a summary. Those codes are the strongest evidence on
this beat because they are the *contracting officer's* classification, not the vendor's
marketing — an ICE award buying a "REALISTIC DE-ESCALATION INSTRUCTOR COURSE" coded
`U013 "EDUCATION/TRAINING-COMBAT"` states the purpose/effect gap inside one record.

**Need a dollar total?** `usaspending --sum` pages the award list and tells you whether
the figure is complete or a floor. Do not hand-scrape one.

**Never add two `--sum` totals together without checking for shared award IDs.**
`recipient_search_text` is a search, not an entity resolver: a parent-company name matches
subsidiary records. Querying a PARENT can return records whose recipient is a SUBSIDIARY, and the
two queries share 55 of their top 100 PIIDs — summing them produced a $10.7B figure that
sat in an edited draft until someone checked. The tool now warns, but read
`meta.recipient_names_matched` before citing any total.

**Attributing an assistance award to a vendor? Check the UEI.**
`usaspending` recipient matching is precise on CONTRACTS but much fuzzier on
grants, loans and direct payments. A worker checking one vendor found 14 "extra"
awards that were all unrelated SBA COVID-era small businesses with similar names
— different UEI, different state. Also: list one `--group` at a time (contracts,
idvs, grants, loans, direct_payments, other_assistance); the API rejects a mixed
listing, though `--count --all-types` accepts one.

**Counting federal money?** `usaspending --count` returns a real total by award
type. Two modes, and the difference decides whether your number means anything:
the default matches the recipient **name** (precise — use it for a vendor total),
while `--keywords` is full-text and **fuzzy** (searching a two-word name returns
NURAD Technologies, because the words appear somewhere in the record). Keyword
counts carry a caveat in the output; do not report one as a vendor total.

**A quoted firm name on `courtlistener` matches ATTORNEY-OF-RECORD appearances, not
party status.** A worker nearly reported a firm as a litigant on that basis. Check which
role the match reflects before writing it up.

**`fedreg` is relevance-ranked, not a filter.** A query returns ~20 rows whether or not
they are all relevant; the top hits are the match, the tail is noise. "This query returned
N documents" is not a count of anything — use `meta.total_matches` for a real total.

**Counting filings? Quote the phrase, and read the total off the header.** A
source that knows its true total now prints it directly:
`results:  20 of 293 total_matches  (this page only -- cite 293, not 20)`.
Cite that number, not the row count — "at least 20" is almost never the honest
answer. (It is still on `meta.total_matches` in `--json`, nested inside the
first result; you no longer need to go looking for it.) And CourtListener
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

Fall back to `--text` or plain WebFetch only when the question is genuinely
open-ended and the whole document must be read.

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

`AccessBlocker` names the mechanism. Browser-passable walls (Cloudflare,
Turnstile, reCAPTCHA, DataDome, JS-only) escalate to a real browser
automatically. If the browser also fails, the job parks as `AwaitingHuman` with
a resume token rather than dying.

**Do not treat a gate as a dead end, and never as an absence.** Record the token
in your work log and move on; Mark clears gates in batches via `$CS gate ui`.

**A 404 is not a wall.** It prints `NOT a negative finding, and NOT a block —
the URL does not exist`, and it means you guessed the path wrong (`/about` vs
`/about-us`) or, when sweeping a sequential id range, that the id was never
issued or has been pruned. Do not read it as suppression: two workers nearly
wrote that a publication was blocking its own about-page. Check the path and
retry. The exit code is still 2 — a 404 is never a verified absence.

## Report the friction

**This is the second half of the task**, and it is not optional. cascade-search
is new and under active evaluation; your account of where it confused you is the
primary evidence for what gets fixed next.

Invoke the `hallway-agent-testing` skill — it carries the method (what counts as
friction, how to write it so it is actionable, how to report a command
faithfully). Log to `FEEDBACK.md` in the cascade-search repo, or open an
issue using the "False negative" or "New source request" template.

The short version: report anything you had to look up or guess, anything
surprising, every error verbatim with its command, **where the results
themselves fell short** (wrong corpus, missing source, ranking that buried the
answer, a number you could not trust), and what actually worked. Report it even
if you worked around it — especially then.

Two cautions specific to this tool:

- **Copy the command, do not reconstruct it.** Two workers independently
  reported "extract exits 0 on AccessBlocker." It exits 2. They had piped to
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
