# evidence-search: per-source notes

Read the section for the source you are using. Each note is a trap a worker hit, and how to avoid it.

## Contents
- Crossref (paywalled articles)
- USAspending: PSC/NAICS, --sum, shared award IDs, UEIs, --count modes, IDVs and transactions
- CourtListener: attorney-of-record matches, watching a docket
- Federal Register ranking
- SAM.gov (pre-award)
- LDA and FEC
- Nonprofit 990s
- SEC EDGAR
- Sanctions

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
**Watching a specific federal docket?** `courtlistener --docket <court>:<number>`
(e.g. `dcd:1:26-cv-01352`, optional `--since YYYY-MM-DD` and `--grep REGEX`)
requires the court slug because bare docket numbers collide across 94 federal
districts. It scrapes the public docket page alongside the RECAP search API so
you get full docket entry text (including text-only minute orders that have no
PDF), `storage.courtlistener.com` PDF URLs, parsed deadlines, and the RECAP
`Last Updated` timestamp. Any `VerifiedAbsence` from `--docket` is explicitly
bounded by that `Last Updated` timestamp — RECAP is crowd-sourced, not real-time
PACER.

**IDV umbrellas vs. task orders on `usaspending`.** An Indefinite Delivery Vehicle
(IDV / BPA) has `$0` obligated on the parent header; the real money lives on its
child delivery/task orders. Passing an IDV PIID (`70CDCR20D00000011`) to
`usaspending` or `usaspending --sum` automatically queries both the parent award
and its child orders, or use `usaspending --idv <PIID>` directly. Use
`usaspending --transactions <generated_internal_id>` for the modification
history (`P00001..N`) and action dates. Note that DoD procurement carries a
statutory 90-day reporting delay on USAspending.

**Pre-award solicitations live on `samgov`, not `usaspending`.** RFPs, RFIs,
Sources Sought, Special Notices, and Sole-Source J&As appear on SAM.gov before
any contract is awarded. Use `samgov <query> [--agency 070] [--notice-type r,p,s]`,
`samgov --detail <opp_id>`, and `samgov --attachments <opp_id>` (pass attachment
download URLs straight into `extract`).

**Lobbying vs. campaign contributions (`lda` and `fec`).**
- `lda` searches U.S. Senate Lobbying Disclosure Act filings (`LD-1` registrations
  and `LD-2` quarterly activity reports, 1999–present) by `--client`,
  `--registrant`, `--lobbyist`, `--issue`, `--query`, and `--year`. It does NOT
  cover LD-203 political-contribution reports, FARA foreign-agent filings, or
  state lobbying commissions.
- `fec` traces federal campaign contributions (`--contributor <name> --recipient
  <candidate-or-committee> --cycles 2006,2008`). If a local FEC bulk SQLite
  index is built (`fec --index-bulk ...`), it answers offline; otherwise it
  queries `/v1/schedules/schedule_a/`, checking both individual (`is_individual=true`)
  and PAC/committee (`is_individual=false`) schedules plus `--employer`. Never
  use `DEMO_KEY` for multi-query sweeps; set `FEC_API_KEY`.

**Nonprofit 990s (`nonprofit`) and entity collisions.** `nonprofit <name>`
queries ProPublica Nonprofit Explorer (`--state`, `--ntee`), and `nonprofit --ein
<EIN> [--year YYYY]` pulls filing financials and IRS 990 PDF links. **Watch the
`entity_caveat` warning:** unrelated nonprofits frequently share a name (e.g.
`"Donors Trust"` matches two distinct EINs in NE and VA), and ProPublica's
fuzzy stemming can match `"DonorsTrust"` to `"Donorsrus Inc"`. Note also that
public Form 990s redact Schedule B donor names and report grants made (Schedule I),
not grants received.

**SEC EDGAR (`sec`).** `sec <query>` runs EFTS full-text search across post-2001
EDGAR filings (`--forms 10-K,10-Q,8-K`, `--since`, `--until`); quote phrases for
exact match. `sec --cik <CIK-or-ticker-or-name>` pulls a company's submission
history and warns if a company name substring matches multiple CIKs. Private
companies, pre-2001 paper filings, and foreign issuers without U.S. reporting
obligations are not in EDGAR.

**Sanctions (`sanctions`).** `sanctions <name> [--list ofac|eu|all] [--exact]`
downloads and indexes the U.S. Treasury OFAC SDN list and the EU Financial
Sanctions Consolidated list into a local SQLite database (`~/.evidence-search/bulk/sanctions/`),
grouping EU rows by `Entity_LogicalId`. Every result is labelled either
`exact_match` (normalized name/alias match) or `possible_match` (token-sort
Jaro-Winkler >= 0.85, which carries a verify-against-DOB/passport warning).
Neither list expands OFAC's 50 Percent Rule (unnamed subsidiaries >=50% owned
by a blocked person are sanctioned by operation of law without appearing on the
list), nor UK OFSI or UN lists.
