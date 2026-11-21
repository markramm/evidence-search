# evidence-search

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

## A negative I could actually publish

In August 2026 I reported on a $244 million federal contract awarded without
competition — the Office of Refugee Resettlement hired a charity with no
documented legal practice to represent unaccompanied immigrant children.

The load-bearing fact in that story is an absence:

> Query the federal spending database by the organization's unique entity
> identifier — every prime contract and every grant, back to fiscal year 2008.
> The result is zero. This contract is the entire federal prime-award history
> of the recipient. *(The query covers prime awards; it would not capture money
> reaching the organization as a subcontractor or through a pass-through.)*

*(The piece is written and fact-checked; at the time of writing it is held
pending responses to requests for comment. The finding below is what the tool
produced, not a claim about where it ran.)*

That parenthetical is the point. Without it the sentence is an overclaim a
lawyer could take apart, because a UEI-level prime-award query genuinely does
not see subawards. With it, the claim is narrow, checkable, and survives.

Getting there needed three things this tool provides and a bare search does not:

1. **A typed negative.** The zero came back as `VerifiedAbsence`, not an empty
   list — which means every engine answered. Had any been rate-limited, the
   result would have downgraded to `RateLimited` and the claim would have been
   unpublishable. That distinction is not a discipline you remember at 2am; it
   is enforced in the type.
2. **A control query.** The same query shape returned eleven awards for the
   displaced incumbent. An absence you cannot contrast against a positive is
   just a query you should not trust.
3. **The scope, printed.** The `ASKED:` block names the exact strings and the
   filters that bounded them, so the caveat in the published sentence was
   copied from the tool rather than remembered.

The same reporting also caught the tool being **wrong**, which is worth saying.
An earlier draft claimed a $244,034,658 figure "appears nowhere in the
contracting database" — because USAspending's API returns `null` for
`base_and_all_options_value`. It is not absent; USAspending simply does not
mirror it. FPDS-NG carries it directly. A null field in one mirror is not an
absence in the record, and no type system catches that for you.

## Outcomes

Every call returns one of these, never a bare list:

| Outcome | Meaning | Exit |
|---|---|---|
| `Hit` | results found | 10 |
| `VerifiedAbsence` | right corpus, right method, every engine answered — **publishable** | 11 |
| `AccessBlocker` | blocked; **names the mechanism**; flags browser-escalatable | 12 |
| `RateLimited` | tooling-limited, **explicitly not content-exhausted** — retry | 13 |
| `AwaitingHuman` | humanomation gate; automate → human → resume | 14 |

The core guarantee, enforced in code: **if any engine was rate-limited or
errored, a verified absence cannot be constructed.** It downgrades to
`RateLimited` automatically. That is `test_dirty_coverage_cannot_yield_verified_absence`.

That guard catches coverage a source **declares** dirty. It cannot catch a
source that failed and didn't notice — which is how three false absences shipped
with the suite green (an AWS WAF soft block read as a document, unparseable
award amounts read as no awards, and a cached absence that lost its own scope;
all fixed, see the 2026-09-19 entry in `FEEDBACK.md`). The generic backstop is
`tests/test_every_source_absence.py`, which sweeps **every** source entry point
against transport failure, rate limiting and a malformed payload. Adding a
source means adding one line to its `ENTRY_POINTS` table.

## Install

```bash
git clone https://github.com/markramm/evidence-search.git
cd evidence-search
python3 -m venv .venv && ./.venv/bin/pip install -e .
```

Retrieved documents are archived to `~/.evidence-search/archive` by default;
set `EVIDENCE_ARCHIVE` to point it at your own corpus. The call ledger and
cache live in `~/.evidence-search/store.db`; override with `EVIDENCE_DB`.

Upgrading from `cascade-search`? If `~/.cascade-search/` is the only one of the
two that exists, it is used as-is, so an existing ledger, archive and any open
gates keep working. The old `CASCADE_ARCHIVE` / `CASCADE_DB` variables are still
honoured.

## Use

```bash
evidence-search news '"Desert Snow" interdiction'
evidence-search oscn --county caddo --lname Frazier --year 2013
evidence-search oscn --county caddo --case CF-2013-00038      # archives w/ SHA-256
evidence-search courtlistener "Desert Snow" --type r
evidence-search courtlistener --docket dcd:1:26-cv-01352 --since 2026-09-01
evidence-search propublica "Blue Owl"
evidence-search usaspending "GEO Group" --agency ICE --count
evidence-search usaspending --idv 70CDCR20D00000011           # delivery/task orders under an IDV
evidence-search usaspending --transactions CONT_AWD_...       # modification history (P00001..N)
evidence-search samgov "detention" --agency 070               # SAM.gov contract notices
evidence-search lda --client "Purdue Pharma" --year 2008      # Senate LD-1/LD-2 lobbying filings
evidence-search fec --contributor "Purdue" --recipient "Rogers" --cycles 2006,2008
evidence-search nonprofit "Donors Trust"                      # IRS Form 990 index + PDFs
evidence-search sec '"CoreCivic"' --forms 10-K,10-Q,8-K       # SEC EDGAR EFTS full-text
evidence-search sec --cik CXW --forms 8-K                     # SEC company submission history
evidence-search sanctions "Intellexa"                         # OFAC SDN + EU Consolidated bulk lists
evidence-search fara --country "El Salvador"                  # DOJ FARA foreign principals & registrants
evidence-search wayback "https://vendor.example.com/contracts" --collapse-digest
evidence-search openalex --author "0000-0002-1234-5678" --retracted-only
evidence-search socrata "detention" --domain data.cityofnewyork.us --dataset abcd-1234
evidence-search arcgis "Holdings LLC" --layer-url https://.../FeatureServer/0 --field OwnerName
evidence-search montana_cadastral "Holdings LLC"              # shipped ArcGIS state parcel plugin
evidence-search kev "Ivanti" --ransomware-only                # CISA KEV + NIST NVD CVE 2.0
evidence-search crtsh "%.contractor.gov" --active-only       # Certificate Transparency SAN discovery
evidence-search epa "Exxon" --state TX --violators-only       # EPA ECHO compliance & non-compliance qtrs
evidence-search openfda "infusion pump" --endpoint recall     # OpenFDA recalls, 510(k)s, enforcement
evidence-search cfpb "fees" --company "SECURUS TECHNOLOGIES"  # CFPB consumer complaints & narratives
evidence-search icij "Holdings" --type entity                 # ICIJ Offshore Leaks bulk SQLite index
evidence-search brokers "LiveRamp" --state CA                 # CA, VT, TX, OR data-broker registries
evidence-search gdelt "border surveillance" --country US      # GDELT DOC 2.0 global news discovery
evidence-search plugin check [--live]                         # 6-point absence-safety audit (+ live canary)
evidence-search plugin init tx_contracts --driver socrata --domain data.texas.gov --dataset abcd-1234
evidence-search sources                                       # every source's tier, counts, and scope
evidence-search limits                                        # usage, 429 blocks, backoff
evidence-search --wait-max 90 courtlistener '"Desert Snow"'   # queue for a slot, up to 90s
evidence-search --json news 'query' | jq '.results[].url'
```

Exit codes encode the outcome, so shell callers branch without parsing.

**Outcomes are 10 and above. Nothing else is.** That separation is the whole
point: exit 1 is what Python returns on an uncaught exception, what argparse
returns on a bad flag, and what the shell means by "it failed." If a negative
finding shared that code, a crash in a fetch loop would read as a certified
absence. It used to. It doesn't now.

| Code | Meaning |
|---|---|
| `10` | `Hit` |
| `11` | `VerifiedAbsence` — publishable as scoped |
| `12` | `AccessBlocker` — **not** a negative finding |
| `13` | `RateLimited` — retry; not content-exhausted |
| `14` | `AwaitingHuman` — a gate was queued |
| `1` | uncaught exception. We never return it deliberately. |
| `2` | usage error |
| `3` | no local data — an empty shelf in *our* store, never a claim about the world |
| `70` | internal error (`EX_SOFTWARE`) |

```bash
evidence-search oscn --county caddo --lname Smith --year 2013 >/dev/null
case $? in
  10) echo "found" ;;
  11) echo "verified absence — publishable as scoped" ;;
  12) echo "BLOCKED — not a finding; record the mechanism" ;;
  13) echo "rate-limited — retry, do not write this up as absence" ;;
  14) echo "human gate queued" ;;
   *) echo "tool failure — say nothing about the world" ;;
esac
```

### What a negative does and does not cover

`VerifiedAbsence` output leads with `ASKED:` — the exact strings sent, with the
filters that scoped them — before it prints any verdict. That order is
deliberate. Typed outcomes make *execution* auditable; nothing in the type
system makes *specification* auditable, and the tool will certify a
perfectly-run search for the wrong string.

```
ASKED:
  'Frazier'   [db=caddo, year=2013]   via oscn
  ^ this is an absence OF THESE STRINGS. A variant spelling,
    married name, or transliteration is a DIFFERENT question.
```

In `--json`, the same list is at the top level under `asked`, not buried in
`probes[]`. A consumer deciding whether to trust a negative should not have to
walk the structure to find what the negative is about.

## Sources

| Source | Auth | Limits | Notes |
|---|---|---|---|
| `news_rss` | none | 20/min | Google News RSS. Worked when WebSearch was dead. |
| `oscn` | none | 8/session, 2s | Turnstile engages ~10 fetches. GET form, `db=<county>`. |
| `courtlistener` | optional token | **5/min · 50/hr · 125/day, concurrent** | `storage.courtlistener.com` serves PDFs where `/recap` 403s. `--docket <court>:<number>` watches entries & deadlines. |
| `propublica_disclosures` | none | 15/min | SvelteKit `__data.json`; param is **`q=`** not `search=`. |
| `docs` | none | 25/min | Documentation `llms.txt` indexes. Sites: `claude-code`, `claude-api`. |
| `web` | none (opt. key) | 30/min | Zero-daemon general web search (keyless Mwmbl default; optional Brave/Serper/Tavily keys or `SEARXNG_URL` override). |
| `usaspending` | none | 30/min | Federal awards. Returns a REAL total by award type — plus `--agency`, `--idv`, `--transactions`. |
| `fedreg` | none | 30/min | Federal Register rules/notices/EOs. `count` is a real total. |
| `crossref` | none | 40/min | Scholarly records. DOI lookup is exact; title search is FUZZY. |
| `lda` | optional key | 15/min · 120/hr | U.S. Senate Lobbying Disclosure Act (`lda.senate.gov`) LD-1/LD-2 filings (1999–present). |
| `fec` | `FEC_API_KEY` or `DEMO_KEY` | 30/min · 900/hr | Federal campaign finance. Prefers FEC bulk SQLite index for donor→committee; falls back to `/v1/schedules/schedule_a/`. |
| `samgov` | none | 20/min · 300/hr | SAM.gov Contract Opportunities (solicitations, presolicitations, special notices, sources sought, awards, attachments). |
| `nonprofit` | none | 20/min · 400/hr | ProPublica Nonprofit Explorer IRS Form 990/990-EZ/990-PF index, financials, and PDF links. Warns on multi-EIN collisions. |
| `sec` | none (descriptive UA) | 8/s · 120/min | SEC EDGAR EFTS full-text search (2001–present) and `data.sec.gov` company submission history by CIK/ticker/name. |
| `sanctions` | none | 10/min (bulk download) | Tier 3 local SQLite index of U.S. Treasury OFAC SDN (`sdn.csv`) + EU Financial Sanctions (`Entity_LogicalId` alias grouping). |
| `fara` | none | 10/min · 120/hr | U.S. DOJ Foreign Agents Registration Act (`efile.fara.gov`) foreign principals and registrants (bulk SQLite + API). |
| `wayback` | none | 15/min · 300/hr | Internet Archive Wayback Machine CDX historical URL capture index, SHA-1 digests, and raw extractable URLs. |
| `openalex` | none (polite `mailto`) | 30/min · 1000/hr | OpenAlex ~250M scholarly works, author ORCIDs, institution RORs, grant funders, and **Retraction Watch** flags. |
| `socrata` | optional `SOCRATA_APP_TOKEN` | 20/min · 400/hr | Generic Socrata / Tyler SODA v2 open-data portal driver (`nyc_contracts`, `chicago_contracts` shipped). |
| `arcgis` | none | 20/min · 400/hr | Generic Esri ArcGIS REST `FeatureServer` / `MapServer` driver (`montana_cadastral` shipped). |
| `rest` (`datagov_catalog`) | none | configurable | Generic declarative JSON REST manifest driver (`datagov_catalog` shipped; `plugin init --driver rest`). |
| `investigate` | none | multi-source | Coordinated multi-source sweep with automatic CIK/EIN/UEI/domain pivots. |
| `mcp` | none | — | Zero-dependency Phase 2 MCP JSON-RPC 2.0 stdio server (`evidence-search-mcp`). |
| `kev` | none | 15/min · 300/hr | CISA Known Exploited Vulnerabilities (KEV) JSON catalog + `--nvd` NIST NVD CVE 2.0 API. |
| `crtsh` | none | 10/min · 200/hr | crt.sh Certificate Transparency X.509 logs for subdomain & shared-SAN discovery. |
| `epa` | none | 20/min · 400/hr | EPA ECHO regulated facilities (Clean Air/Water/RCRA), non-compliance quarters & penalties. |
| `openfda` | none | 40/min · 1000/hr | OpenFDA device recalls, 510(k) clearances, enforcement & FAERS (`404 NOT_FOUND` = recognized empty). |
| `cfpb` | none | 25/min · 500/hr | CFPB Consumer Complaint Database search & narratives by company, product, or state. |
| `icij` | none | 10/min (bulk SQLite) | Tier 3 local SQLite index of ICIJ Offshore Leaks (`full-oldb.LATEST.zip`) with 1-hop relationships. |
| `brokers` | none | 15/min (bulk SQLite) | CA (CPPA), VT (SOS), TX (SOS), and OR (DCBS) statutory data-broker registries w/ DBA normalization. |
| `gdelt` | none | 20/min · 400/hr | GDELT DOC 2.0 global news article discovery API (`counts=False`, `exact_match_supported=False`). |
| `browser` | none | — | Playwright (or `EVIDENCE_BROWSER_CDP`), **public-records hosts only** (allow-listed). |
| `extract` | none | — | Local dynamic filtering: fields, not pages. |

Run `evidence-search sources` (or `evidence-search --json sources`) to inspect the
canonical `SourceSpec` registry (`core/registry.py`) — every source's tier,
rate-limit policy, whether its totals are countable, whether it honours exact
phrases, and the default `corpus`, `not_searched`, and `caveats` attached to
its `VerifiedAbsence` outcomes.

Set `COURTLISTENER_TOKEN` for the authenticated tier, or put the token in
`~/.config/courtlistener/token` (mode 600; `COURTLISTENER_TOKEN_FILE` overrides the
path). The variable wins when both are set. The file is the one to use for agents
and scheduled jobs, whose shells do not read your login profile.

## Browser escalation and humanomation

When a source returns `AccessBlocker` with a browser-passable mechanism
(Cloudflare, Turnstile, reCAPTCHA, DataDome, JS-only), the client **escalates to
a real browser automatically**. If the browser also cannot clear the gate, the
job is parked as `AwaitingHuman` with a resume token — not a dead end.

That is the humanomation loop: **automate to the gate, let the human do only
what a human must do, automate again from there.**

```bash
evidence-search oscn --county caddo --lname Frazier --year 2013
#   -> AwaitingHuman, token 0e96cf371939

evidence-search gate ui        # <- the easy path: worklist in a browser
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
evidence-search gate list
evidence-search browser <url> --headed --save /tmp/page.html
evidence-search gate resume 0e96cf371939 --file /tmp/page.html
evidence-search gate cancel 0e96cf371939 --reason "duplicate test run"
evidence-search gate cancel --older-than 7d --source oscn
```

When an external Chromium/CDP endpoint is available (for example,
[Obscura](https://github.com/h4ckf0r0day/obscura) via `deploy/obscura.sh up` or a
locally running Chrome with `--remote-debugging-port`), set
`EVIDENCE_BROWSER_CDP=ws://127.0.0.1:9222`. Ungated `JS_ONLY` SPA shells hit
by `extract` will render through that CDP endpoint and disclose `rendered:
cdp:<endpoint>` in the provenance header, while CAPTCHA/Turnstile/Cloudflare
blocks still escalate to the humanomation gate queue (and Obscura's
anti-detection / `--stealth` flags are intentionally never enabled).

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
evidence-search extract case.html \
  --grep "FRAZIER, ANDREA" --grep TRAFFICKING --grep "PAUPER|INDIGENT" --grep DISMISS
# raw ~17,371 tok -> extracted ~841 tok (95.2% reduction)

evidence-search extract https://example.gov/page --ids      # award IDs, dockets, UEIs, statutes
evidence-search extract page.html --tables                  # registry rows, no markup
evidence-search extract page.html --text                    # chrome stripped
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

## How it behaves on other people's servers

This hits court systems, government registries, and public APIs. Those are
public records, and the tool is built to read them the way a careful person
would — not to extract at machine speed because it can.

**What it actually does, not what it promises:**

- **Identifies itself.** Every request carries a `evidence-search` User-Agent.
  Crossref gets a `mailto` in the UA because they ask for one — that is their
  documented "polite pool," and joining it costs nothing.
- **Rate limits are per-source, declared, and enforced before the fetch, not
  after.** Run `evidence-search limits` to see every ceiling and current usage.
  They are set from what each source publishes, and where a source publishes
  nothing, from what its behaviour implies: OSCN engages Turnstile after
  roughly ten fetches in a session, so the cap is 8 over a 30-minute window —
  deliberate headroom, not the maximum we could get away with.
- **A paid tier can be declared.** The built-in ceilings are what a source
  publishes for everyone. If your account has a higher one, say so in
  `~/.evidence-search/limits.conf` (or `EVIDENCE_SEARCH_LIMITS_FILE`), one source
  per line:

  ```
  courtlistener = 20/min, 250/hr, 1000/day
  ```

  `EVIDENCE_SEARCH_LIMITS` takes the same text, `;`-separated, and wins over the
  file. An override replaces the budget windows only; spacing, session caps and
  backoff stay as built. A line that cannot be read stops the tool instead of
  falling back, and `evidence-search limits` marks every overridden source. Set
  a number your account does not have and the source will rate-limit you.
- **The ledger is shared and reservation is atomic.** Twelve parallel agents
  draw from one budget in a single SQLite ledger with `BEGIN IMMEDIATE`. Fan-out
  cannot be used to multiply your way past a limit, which is the usual way
  polite tooling becomes impolite.
- **`extract` and `browser` are in the ledger too.** A fetch of an arbitrary
  URL claims a slot before it goes out. If the host belongs to a source, the
  source is charged — an OSCN page read with `extract` counts toward OSCN's
  session cap, because the Turnstile threshold counts it. CourtListener is
  charged only for `/api/` URLs; its pages and `storage.courtlistener.com` PDFs
  are not part of the API budget. Every other host gets its own bucket: one
  request a second and 30 a minute, across all processes. `extract` and
  `browser` sleep through up to 10 seconds of that on their own, then return
  `RateLimited` (13).
- **A 429 stops everyone, not just the process that got it.** The
  `Retry-After` (seconds or an HTTP date; 60s when absent, capped at a day) is
  written to the ledger, and every process's next reservation for that source
  is refused until it passes, with the time left as the retry-after.
- **Caches for 24 hours by default.** The cheapest courtesy is not asking twice.
- **Backs off instead of retrying.** A rate limit returns `RateLimited` (13) and
  stops. There is no retry loop, because a retry loop against a struggling
  server is how a research tool becomes a load test. Two consecutive failures
  of a source start an escalating, shared backoff; a refusal that was about
  the request itself (a 400, 401 or 404) does not count toward it.

**The browser tier is deliberately narrow.** Escalation to a real browser is
restricted by an `ALLOWED_HOSTS` allow-list to court systems and government
registries. It exists because CAPTCHA-walled public-records UIs frequently sit
in front of data the same agency publishes ungated elsewhere, and a human with
a browser is permitted to read those records. It is not a general-purpose
bypass, and it will not run against a host that is not on the list.

Run `evidence-search hosts` to see the current boundary. Operators extend it in
`~/.evidence-search/allowed_hosts.json` (or `$EVIDENCE_ALLOWED_HOSTS`) as
`{"host": "why this is a public record"}` — **the reason is required**, and an
entry without one is refused, so the list cannot decay into undocumented
hostnames. The matching rule is unchanged for configured hosts: the boundary is
the dot, so allowing `sos.example.gov` never allows `evil-sos.example.gov`. A
malformed config fails closed to the shipped baseline, which a config file can
extend but never reduce.

**What this does not do:** it does not evade paywalls, forge sessions, rotate
identity to defeat rate limits, or fetch anything behind a login. When a wall
is a wall, the tool returns `AccessBlocker` and names the mechanism. That is
the whole design — a blocked request is a *reportable fact*, not an obstacle to
route around.

**On robots.txt:** the tool does not currently parse it. For the government
APIs and public dockets it targets, the operative constraints are the published
rate limits and terms it already honours. If you point it somewhere else, that
is your call to make and your terms to read.

**If you operate one of these sources and the behaviour here is wrong for you,
open an issue.** The limits are configuration, not conviction.

## What this does NOT do

**It is not a primary general web search index** — though `web` covers open web
discovery without needing a background daemon or container runtime. It focuses
on primary public-record sources: courts, contracts, disclosures, news, docs,
and pluggable local/state portals.

## General web, zero daemon required

```bash
evidence-search web "query"
```

Out of the box, `web` uses the keyless **Mwmbl** JSON REST API directly in
process — no local container, no Flask daemon, and no API key needed. If you
want broader commercial recall or already operate a SearXNG instance,
`--provider auto` automatically picks them up when configured:

* `BRAVE_API_KEY` -> Brave Web Search API (`--provider brave`)
* `SERPER_API_KEY` -> Serper Google Search API (`--provider serper`)
* `TAVILY_API_KEY` -> Tavily Search API (`--provider tavily`)
* `SEARXNG_URL` or `--base URL` -> optional SearXNG instance (`--provider searxng`)

Because Mwmbl returns a bare `[]` on zero hits, `web` refuses to certify a
`VerifiedAbsence` on `[]` unless a positive control probe (`wikipedia`)
confirms the endpoint is live and answering.

SearXNG's adapter (`engines/searxng.py`) remains supported whenever `--base`,
`SEARXNG_URL`, or `--provider searxng` is selected, reconstructing coverage from
`/config` and `unresponsive_engines`:

```
queried    = engines enabled on the instance   (/config, cached)
failed     = unresponsive_engines              (per search)
responsive = queried - failed
```

A SearXNG sweep where upstream engines were throttled downgrades to
`RateLimited` and is never cached. Regardless of provider, `web` treats quoted
phrases as fuzzy discovery leads rather than an exhaustive census.

**`usaspending` is the counting source.** Where `web` cannot count and
`courtlistener` counts filings, this counts federal money:

```bash
evidence-search usaspending "Relentless LLC" --count     # 52 awards, by type
evidence-search usaspending "Relentless LLC" --limit 10  # the awards themselves
evidence-search usaspending "detention" --keywords --count
```

Two search modes, and the difference decides whether the number means anything.
`--recipient` (default) matches the recipient NAME — precise, and the right mode
for a vendor total. `--keywords` is full-text across award descriptions and is
FUZZY: searching "Force Science" returns NURAD Technologies, because the words
appear somewhere in the record. Keyword counts therefore carry an explicit
caveat, because a count of records *containing* words is not a count of awards
*to* anyone.

**Know which sources can count.** This distinction has produced the same bug
three times, so it is now explicit per source:

| Source | Counts? | Why |
|---|---|---|
| `usaspending` | **yes** | defined corpus, real totals by award type |
| `fedreg` | **yes** | defined corpus of government documents |
| `courtlistener` | **yes**, quoted | reports `total_matches`; quote the phrase |
| `lda` | **yes** | Senate LDA API reports real `count` across LD-1/LD-2 filings |
| `fec` | **yes** | bulk SQLite index or Schedule A `pagination.count` |
| `samgov` | **yes** | SAM.gov search API reports `page.totalElements` |
| `nonprofit` | **yes** | ProPublica Nonprofit Explorer reports `total_results` (verify EIN on collisions) |
| `sec` | **yes**, quoted | EDGAR EFTS reports `hits.total.value`; quote phrases for exact match |
| `sanctions` | **yes**, `--exact` | local SQLite index of OFAC SDN + EU Consolidated; fuzzy matches are `possible_match` |
| `crossref` | **no** | matches loosely across ~150M records |
| `web` | **no** | engines ignore quotes; totals are not countable |

Crossref is a **resolver**, not a search index. A DOI lookup is exact and
authoritative — it returns the journal, year, and every author, which is usually
what a citation needs even when the publisher's full text is walled. Its *title*
search is discovery only: probing "Forced Science" returned "Forced to Pursue
Science: Entity List Triggers" as the top hit, so every non-exact result carries
a verify-before-citing warning.

```bash
evidence-search crossref 10.1177/10986111251357498   # exact, authoritative
evidence-search crossref "Forced Science"            # fuzzy, verify the match
evidence-search fedreg "immigration detention" --type rule
```

**`web` is discovery, not census.** Its upstream engines largely ignore quoted
phrases, and `web` does not assume a running local SearXNG container. Result sets
are fuzzy and totals are not countable.

We deliberately do **not** filter locally to compensate. Filtering one page of an
N-page result set produces a number that looks like a count and is not one:
narrowing 30 results to 23 says nothing about the other pages. That is the
page-cap bug in a new place, and a bad count is worse than no count. Instead the
tier says what it is, and a `web` absence is labelled weaker than one from a
corpus with known query semantics.

## Cross-source pivoting & MCP server

```bash
evidence-search investigate "CoreCivic" --domain corecivic.com
evidence-search mcp --list-tools
evidence-search-mcp   # stdio JSON-RPC 2.0 server
```

`investigate` runs a coordinated sweep across core registries, extracts
discovered identifiers (`CIK`, `EIN`, `UEI`, `award_id`/`piid`, `dockets`, and
domains), automatically fires secondary deep-dive pivots (`sec.submissions`,
`nonprofit.by_ein`, `crtsh.search`), and exits `12` (`AccessBlocker`) rather
than `11` (`VerifiedAbsence`) whenever any requested source fails. `mcp`
(`evidence-search-mcp`) exposes `evidence_sources`, `evidence_search`,
`evidence_investigate`, `evidence_extract` (including PDF/OCR and CDP),
`evidence_record`, and `evidence_limits` over JSON-RPC 2.0 stdio.

## Design notes

**Rate limits are shared state, and reservation is atomic.** Cache, limiter,
and job queue live in one SQLite DB (`~/.evidence-search/store.db`, WAL mode)
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

`--wait-max SECONDS` (default 0, off) is for contention. Under fan-out every
agent used to be refused at the same moment and retry at the same moment. With
it, a call sleeps until the slot it was refused should reopen, plus a random
extra so waiters do not wake together, and claims again through the same
atomic reservation; whoever loses is told the next reopening and sleeps again.
There is no queue, and nothing but the ledger grants a call, so no window can
be exceeded. It waits for spacing, the per-minute window, a shared 429 block
and a failure backoff, each only when the wait ends inside the time left. An
hour or day window, or a session cap, returns `RateLimited` immediately with
the time the window reopens.

**`limits` says why a call is refused.** Per source: usage in every window
(the session cap too), `blocked:` with the deadline of any shared 429 block,
`backoff:` with the failure streak and its reason, and `next call:` — either
`allowed` or `REFUSED` with the cause and the seconds left. Host buckets used
in the last hour are listed after the sources. `--json limits` adds `usage`,
`session`, `min_interval_s`, `blocked_until`, `blocked_for_s`,
`blocked_reason`, `backoff` and `next_call` to each row; the existing fields
are unchanged.

**Sharing the ledger with another tool.** A second program that spends the
same upstream budget can reserve through this one instead of keeping its own
count:

```bash
evidence-search limits reserve courtlistener   # exit 0: go ahead; 13: refused, do not call
evidence-search limits block courtlistener 120 # that tool got a 429 with Retry-After: 120
```

`reserve` also takes a URL and charges the bucket that owns it. It honours
`--wait-max` and `--json` like any other command.

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
evidence-search record --list --source courtlistener
evidence-search record courtlistener:5409345
evidence-search record courtlistener:5409345 --fields party,recap_documents
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

**`cacheable=False` where terms forbid storing results.** `Store.put()` refuses
to write a payload from a source whose policy clears the flag — we record the
call, never the results. No source in `POLICIES` sets it today (the Brave plans
that motivated it are gone), so this is enforcement waiting on a metered source
rather than behaviour you can currently observe.

## Status

Phase 1, and honestly labelled: this is a tool built for one reporter's beat,
in the open, with its warts logged in `FEEDBACK.md`. **1,059 offline tests passing**
(1,065 total including local HTTP gate-UI socket tests) on Python 3.10-3.15.
Browser escalation, the humanomation gate (open/list/resume/cancel),
local extraction, declarative JSON manifest plugins, and the 6-point plugin
conformance harness are built and working.

Not yet built: async job-queue consumption, cross-source federated merge
(Phase 3b — ranking one result set across `web`/`news`/`courtlistener`
together), local semantic layer.

## Using it from an agent

`skills/evidence-search-usage/` is a [Claude Code skill](https://docs.claude.com/en/docs/claude-code/skills)
that teaches an agent to drive this tool — which command answers which question,
how to read the coverage line before trusting a result, when an empty result is
a finding and when it is a tooling failure, and how to use `extract` instead of
pulling pages into context.

Install it by symlinking (or copying) into your skills directory:

```bash
ln -s "$PWD/skills/evidence-search-usage" ~/.claude/skills/evidence-search-usage
```

It pairs with [`hallway-agent-testing`](https://github.com/markramm/ramm-agent-skills),
which carries the method for logging friction back here. That pairing is not
incidental: most of the bug fixes in this repo came from agents reporting where
the tool confused them mid-task, not from tests written in advance.

## Contributing

Field reports are the most valuable contribution here. `FEEDBACK.md` is a
running log of friction found by agents actually using the tool on real
research tasks — most of the bug fixes in the history came from it rather
than from synthetic tests. Bug reports that name the *task you were doing*
when the tool failed you are worth more than ones that only name the symptom.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
