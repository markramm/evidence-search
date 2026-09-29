# Extension candidates, ranked by what live reporting needs

Status: analysis, 2026-09-29. Tracks against **EPIC #7** (source expansion, triaged by access
shape): https://github.com/markramm/evidence-search/issues/7

EPIC #7 triaged a ~350-source inventory by **access shape**. This document asks a narrower question:
**for the stories one newsroom is working this week, which extensions would change what gets
published, and what would a `VerifiedAbsence` from each one be worth?** The findings come from using
the tool on 2026-09-29 against eight live investigations, running about 35 typed calls. Where a
source did not exist, we record what we did instead.

Any new source below should declare `corpus`, `not_searched`, `caveats` and
`exact_match_supported` up front, as the source-plugin design note proposes
(`docs/source-plugin-design.md`, commit b0969f3). Candidate 2 shows why: an incomplete
`not_searched` let a correct search certify a false negative.

The investigations are unpublished, so they are described here by **shape**, not subject:

| Shape | What it needs from the tool |
|---|---|
| **A. Detention contracting** (a county intermediary contract, a competed ICE turnkey RFP, a multi-award construction IDIQ, a GAO facilities report) | solicitations, awards, task orders under IDVs, vendor 10-Qs, habeas litigation per facility |
| **B. Candidate money** (a Senate candidate's pharma-PAC receipts; thin coverage of several battleground races) | itemized PAC → committee transactions, leadership-PAC outflows, candidate financial disclosures, lobbying filings naming a bill |
| **C. Election enforcement** (voter-citizenship database checks, an investigative-agency surge, related federal suits) | docket watch on fast-moving D.D.C. cases, an RFI → award check, Privacy Act notices |
| **D. Think-tank / dark-money history** | 990 Schedule I grants between named nonprofits, with EIN resolution |
| **E. Data-broker enforcement** (a state regulator's enforcement actions; an open opt-out list) | state data-broker registries, enforcement orders, litigation against the regulator |
| **F. Financial rulemaking** (crypto rules at SEC/OCC) | rule status monitoring, comment dockets, appointee disclosures |
| **G. AI-agent security incidents** | news/web; little structured-source value |

## Ranked candidates

Legend. **Access**: keyless / free-key / bulk / paid / scrape (browser tier). **Effort**: S ≤ 1
day, M ~2-4 days, L a week or more. "VA worth" means how publishable a `VerifiedAbsence` from the
source would be, and under what scope.

### 1. FEC campaign finance, bulk first (**NEW**, not in #1-#6; not listed in #7)

- **Adds:** itemized committee → committee and committee → candidate transactions, with *both* sides
  of each transaction (the giver's Schedule B and the recipient's Schedule A), committee IDs,
  candidate IDs and image numbers.
- **Access:** **bulk, keyless, verified 2026-09-29.** `https://www.fec.gov/files/bulk-downloads/<year>/{pas2,oth,indiv}<yy>.zip`.
  `pas2` and `oth` run 9-36 MB per cycle. The OpenFEC API is free-key (api.data.gov). `DEMO_KEY`
  returned `429` with `Retry-After: 34289` on the first call of the day. That matches the
  2026-08-28 FEEDBACK entry, which found it exhausted by unrelated traffic before any use.
- **Stories:** B directly; D (PAC money around the think-tank network); A (vendor PACs).
- **What we did instead:** answered a live question ("did PAC X give to leadership PAC Y, and is
  the count of N contributions over years Z right?") in minutes from six cycles of `pas2` + `oth`.
  The answer turned on two things a name search gets wrong:
  1. A same-surname candidate in another state, which only the committee ID and candidate ID
     disambiguate. That contribution was later refunded, which shows only on a line-16 refund row.
  2. The two sides of one contribution carrying different dates. Counted naively, one gift
     becomes two.
- **VA worth: high, if scoped.** "No transactions between committee X and committee Y in cycles
  C1-C2 in FEC bulk data" is a precise, publishable negative. Committee IDs are exact and the
  corpus is a defined census of filed reports. The limits must be in `not_searched`: unitemized
  receipts under the itemization threshold; paper-era filings; transactions reported only by the
  side that files (a candidate committee's PAC receipts did not appear in `oth` in our pull, so
  only the donor side confirmed them); and reports not yet processed for the current cycle.
- **Others:** every political newsroom and campaign-finance researcher. OpenSecrets-style
  reconciliation, reproducible with image numbers, is rare outside large data desks.
- **Effort:** M. It uses the same local-index shape as sanctions (#3) and ICIJ (#5); build it on
  one indexer. An optional OpenFEC path with a *registered* key covers recency. Name-based
  queries must warn on multiple committee IDs, the same double-count trap as the usaspending
  recipient warning.

### 2. usaspending: IDV-aware counts, child awards under a vehicle, PIID-shape routing (**NEW** bug + extension on an existing source)

- **A false `VerifiedAbsence` found in use.** `usaspending "70CMSW26D00000016" --keywords --count`
  → **VerifiedAbsence (exit 11)** for an IDIQ that exists (award 362519505). The same query with
  `--all-types` → Hit, `idvs: 1`. `count()` defaults to `CONTRACT_TYPES = ["A","B","C","D"]`, and
  the absence's `not_searched` lists only "awards below the reporting threshold" and "classified
  and otherwise unreported spending". It never says **IDVs, grants, loans and other assistance
  were not searched.** The ASKED line prints `types=A,B,C,D`, but nothing tells the reader that
  this excludes the vehicle they asked about. A vendor-name `--count` has the mirror problem: it
  silently undercounts (1 contract, vs. 1 contract + 1 IDV with `--all-types`). This is the
  absence invariant from #7's standing rule ("a plausible-but-wrong identifier must not return an
  absence").
- **Adds:**
  - (a) `not_searched` names the award groups excluded.
  - (b) PIID-shape routing. Under the uniform PIID format, the 9th character is the instrument
    type: `D` = indefinite-delivery vehicle, `F` = order under one, `R`/`Q` = a *solicitation*.
    A PIID query can therefore default to the right group. For `R`/`Q` it should say "this is a
    solicitation number, never an award ID; try SAM.gov (#2)" instead of certifying an absence.
    `usaspending "70CDCR26R00000026" --keywords --count` returns a VerifiedAbsence today that is
    true and meaningless.
  - (c) `--idv <PIID>`: child awards (task orders) under a vehicle, so that "no orders yet under
    this IDIQ" is a real negative.
  - (d) `--agency`: already requested in FEEDBACK. It is needed to scope "no ICE award matching X
    since date D".
  - (e) A reporting-lag line in `not_searched` for date-bounded queries near today.
- **Access:** keyless (existing).
- **Stories:** A (turnkey RFP, construction IDIQ task orders), C (RFI → award check).
- **VA worth:** it restores the value of every usaspending negative on IDV-heavy beats. As it
  stands, a worker who follows the documented `--count` pattern on an IDIQ is handed a certified
  false negative.
- **Others:** anyone tracking multi-award vehicles (defense, DHS, GSA schedules). This is most
  large federal spending.
- **Effort:** S for (a) and (e); M for (b)-(d).

### 3. SAM.gov: strengthens **#2**

- **Adds (story evidence, beyond what #2 records):** three live checks this week needed SAM.gov and
  could only be approximated elsewhere: whether a competed turnkey RFP has been awarded; whether a
  voter-file RFI produced a contract; and the attachment text that confirmed a facility named in
  the RFP ("Historical Location"). #2's point that "SAM.gov solicitation text is a FOIA substitute"
  held again.
- **Access:** keyless search API, per #2. Attachments/entity pages are JS-only (browser tier).
- **Stories:** A, C, F (agency RFIs).
- **VA worth: high for "no award notice posted as of D"**, the negative that USAspending cannot
  give for an unawarded solicitation (see candidate 2b).
- **Others:** procurement reporters everywhere; local newsrooms watching facilities in their county.
- **Effort:** M (per #2). **Priority raised** by this analysis: it is the missing half of candidate 2.

### 4. CourtListener docket-watch mode (**NEW** issue; corroborates several FEEDBACK requests for `--docket`)

- **Adds:** `courtlistener --docket <court>:<number>` returns entries since a date, the page's
  "Last Updated" and "Date of Last Known Filing" fields, deadlines stated in entries (e.g., "Answer
  due ... by <date>"), and **"notice of related case"** links.
- **What happened in use:** `extract --grep` on a docket page truncates each match at about 200
  characters, which cut off the minute-order text. Reading the page required `--text` plus a hand
  grep. A quoted docket number without `--court` returned **14 dockets in different districts**
  (docket numbers repeat across courts). With `--court dcd` it returned the case *and a related
  case filed 13 days later*, found only because that case's "notice of related case" contains
  the first case's number. That related case was new information for the story.
- **Access:** keyless (existing).
- **Stories:** C (two fast D.D.C. cases before the same judge); A (per-facility habeas counts: a
  quoted facility name returned 961 matches in one district, a number that needs deduplication to
  dockets and nature-of-suit before it counts petitions).
- **VA worth:** "no PI motion on the docket as of its <Last Updated> timestamp" is publishable *with
  the timestamp*. Without the timestamp it is not, because CourtListener is a RECAP mirror.
- **Others:** very high. Docket-watching is daily work for legal-affairs desks and litigation
  trackers.
- **Effort:** M.

### 5. Nonprofit 990s: strengthens **#4**

- **New evidence for #4's name-collision warning.** Probed through `extract` against the ProPublica
  API (keyless, reachable 2026-09-29):
  - `q=DonorsTrust` (one word) returns `total_results: 1`, **"Donorsrus Inc", Jupiter FL**, a
    different organization.
  - `q="Donors Trust"` returns 2 results and ranks **Donors Trust, Omaha NE (26-2515785)** first,
    above **Donors Trust Inc, Alexandria VA (52-2166327)**.

  A client that took the top hit would attribute a donor-advised fund's grants to the wrong EIN.
  One that took an empty spelling as absence would certify a false negative. Either way it is
  the exact trap #4 names.
- **Also for #4:** the research question in shape D is **Schedule I grants between two named
  organizations over a decade**. The search endpoint returns summary fields only, so the
  grant-level path is the e-file XML route #4 mentions (AWS/IRS bulk). That makes #4 Tier 1 for
  lookup but **Tier 3 for the question that matters**.
- **Stories:** D; B (pain-advocacy nonprofits' funding); E (industry associations).
- **VA worth:** low at name level, by design. Moderate at EIN + tax-year level, bounded by #4's
  caveats (churches, sub-threshold filers, 990-N).
- **Others:** very high. Dark-money reporting runs on Schedule I.
- **Effort:** M for lookup; L for Schedule I.

### 6. SEC EDGAR: strengthens **#1**

- **Adds (story evidence):** an award-monitor task on shape A is parked on "the award notice *or*
  the prime vendor's next 10-Q". EDGAR full-text search on the RFP number or facility name would
  catch a vendor disclosing the award before USAspending posts it. For shape F, comment letters
  on SEC proposed rules are on sec.gov, not Regulations.gov.
- **Access:** keyless (per #1).
- **VA worth:** "the vendor's 10-Q does not mention the facility" is strong (EDGAR FTS honours
  quoted phrases, per #1), bounded to 2001 onward.
- **Effort:** M (per #1). No change in priority, but here is a second concrete consumer.

### 7. Congressional financial disclosures + lobbying (LDA): re-probe access (**NEW** issue; LDA is Tier 5 in #7)

- **Adds:** candidate and member financial disclosures (House Clerk; Senate eFD), and LD-1/LD-2
  filings that name a bill number.
- **Why now:** shape B needed "which quarters did a registrant's LD-2 name bill H.R. N". That work
  cited LDA filing UUIDs, which suggests a structured path exists. #7 files LDA under
  "interactive/blocked", so **re-probe it before trusting that tier.** `propublica` covers
  executive-branch appointees only. Candidates in thin battleground races have no disclosure
  source in the tool.
- **Access:** *unverified*. House Clerk disclosures are reported to offer annual bulk downloads;
  Senate eFD is reported to sit behind a terms-acceptance page (browser tier). Probe both and
  record the shapes, as #5 did for ICIJ.
- **VA worth:** for LDA, "no LD-2 in <years> names H.R. N" is a strong, scoped negative. It
  protects a claim that a bill was *not* lobbied, which drafts make and rarely check.
- **Others:** high. Every Hill and influence reporter.
- **Effort:** S to probe; M-L to build.

### 8. State data-broker registries (**NEW** issue)

- **Adds:** the registries that CA, VT, TX and OR maintain of registered data brokers. Joined to an
  open opt-out list, they show which listed brokers are registered, which are not, and which
  registrations are stale. The 2026 CA enforcement actions are largely registration failures.
- **Access:** *unverified*; likely bulk/CSV or HTML per state. Probe first.
- **Stories:** E.
- **VA worth:** "not in the <state> registry as of <date>" is a publishable, regulator-relevant
  negative, provided name variants and DBAs are tried (brokers rename often).
- **Others:** **very high**, and it is open infrastructure: privacy researchers, opt-out tool
  builders and state AGs all use these lists. EPIC #7's rule applies: do not wire the pipeline
  *into* brokers. This is the regulator's list *of* them.
- **Effort:** S per state once probed; M for four.

### 9. Browser allow-list: gao.gov (config, small)

- `extract https://www.gao.gov/products/<report> --ids` → **AccessBlocker (12), http-403**.
  `--browser` was refused: "Host www.gao.gov is not on the public-records allow-list." GAO reports
  are legislative-branch public records, the same tier as the courts and registries already
  allowed. Two earlier passes on the same beat hit the same 403. The FEC request (fec.gov,
  api.open.fec.gov) is already in FEEDBACK 2026-08-28.
- **Effort:** S. It is a default-list decision for the maintainer.

### 10. Lower for these stories (no change to existing issues)

- **Regulations.gov** (free-key, in #7's next tier): useful for agency rulemaking dockets (shape F:
  the banking regulator's rule), less so for SEC.
- **GovInfo / Congress.gov** (free-key, in #7): bill text and cosponsor join dates for shape B. It
  is the other half of the leadership-PAC-to-cosponsor analysis.
- **GDELT (#6), ICIJ (#5), sanctions (#3):** none of the eight stories needed them this week. Their
  priority rests on the transnational side of the beat, not this slate.

## Summary table

| Rank | Candidate | Issue | Access | Stories | VA worth | Others | Effort |
|---|---|---|---|---|---|---|---|
| 1 | FEC (bulk first) | **new** | bulk keyless (verified) + free-key API | B, D, A | high, if scoped | very high | M |
| 2 | usaspending IDV / PIID routing / agency | **new** (bug) | keyless | A, C | restores existing negatives | high | S-M |
| 3 | SAM.gov | #2 (strengthened) | keyless + browser | A, C, F | high | high | M |
| 4 | CourtListener docket watch | **new** | keyless | C, A | high with timestamp | very high | M |
| 5 | Nonprofit 990 / Schedule I | #4 (strengthened) | keyless lookup; bulk for Sched. I | D, B, E | low by name; moderate by EIN | very high | M-L |
| 6 | SEC EDGAR | #1 (strengthened) | keyless | A, F | strong from 2001 | high | M |
| 7 | Cong. disclosures + LDA | **new** (re-probe) | unverified | B | strong for LDA | high | S probe, M-L build |
| 8 | State data-broker registries | **new** | unverified | E | good with DBA variants | very high | S-M |
| 9 | gao.gov allow-list | config | browser | A | n/a | moderate | S |

---

## Draft issues for approval (not filed)

### Draft: FEC campaign-finance source, bulk first

> **Tier 3 (bulk, keyless, verified 2026-09-29), with an optional Tier 2 API path.**
>
> **The research question:** did committee X give to committee Y, when, and does each side's report
> agree? Answered live in minutes from `pas2` + `oth` bulk files (6 cycles, 9-36 MB each) after
> `DEMO_KEY` returned 429 / `Retry-After: 34289` on its first call.
>
> **Produces:**
> - `evidence-search fec --from-committee C… --to-committee C… --cycles 2006-2014` returns rows
>   with the date, amount, report type, image number and transaction ID, from *both* filers where
>   both reported.
> - `evidence-search fec --committee <name>` resolves to committee IDs and warns when several
>   match (same-surname candidates in different states are common).
> - Refund rows (line 16 / negative amounts) are netted and shown, not dropped.
>
> **Absence semantics:** a `VerifiedAbsence` names the cycles indexed and the bulk-file date. It
> lists in `not_searched`: unitemized receipts, transactions reported by one side only, and
> unprocessed recent reports.
>
> **Build on the same local indexer as #3 / #5.** Refs #7.

### Draft: usaspending certifies a false absence for an IDV PIID

> `usaspending "70CMSW26D00000016" --keywords --count` → `VerifiedAbsence` (exit 11). The award
> exists as an IDV (usaspending award 362519505). `--all-types` → Hit, `idvs: 1`.
>
> **Cause:** `count()` defaults to `CONTRACT_TYPES` (A-D), and `not_searched` does not name the
> award groups excluded, so the negative reads as complete.
>
> **Fix:**
> 1. `not_searched` lists the excluded groups whenever `types` is not every group.
> 2. A query shaped like a PIID routes by its 9th character: `D` → include IDVs; `R`/`Q` →
>    "solicitation number, not an award ID; see SAM.gov", never an absence.
> 3. Add a regression test for the IDV case to `tests/test_every_source_absence.py`.
>
> **Follow-ons (separate PR):** `--idv <PIID>` child-award listing; `--agency`; a reporting-lag
> note on date-bounded queries.

### Draft: CourtListener docket-watch mode

> `courtlistener --docket <court>:<number> [--since DATE]` returns docket entries with full text
> (not the ~200-character grep excerpt), the page's `Last Updated` / `Date of Last Known Filing`,
> deadlines found in entry text ("Answer due … by"), and cases linked by "notice of related case".
> A bare docket number matches across districts (14 dockets for one number on 2026-09-29), so
> `<court>` is required. Absence semantics: "no entry matching X as of <Last Updated>", and never
> without the timestamp, because RECAP is a mirror. Refs the `--docket` requests in FEEDBACK.md.

### Draft: re-probe LDA and congressional financial disclosures

> #7 lists LDA as Tier 5 (interactive/blocked). Recent work cited LDA filing UUIDs, which suggests
> a structured path. Probe and record access shapes (as #5 did for ICIJ) for: LDA filings search;
> House Clerk financial disclosures; Senate eFD. Only then scope a client. The key negative is "no
> LD-2 in <years> names H.R. N", which protects claims that a bill was not lobbied.

### Draft: state data-broker registries

> Probe access for the CA, VT, TX and OR data-broker registries and record their shapes. Then
> build `evidence-search brokers '<name>' [--state XX]`, which returns the registration status and
> date, with DBA/variant handling. A `VerifiedAbsence` must state the registry snapshot date and
> the variants tried. This is the regulator's list *of* brokers, which EPIC #7's rule against wiring
> into brokers themselves does not cover. Refs #7.
