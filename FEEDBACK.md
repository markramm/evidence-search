# cascade-search — field feedback

Hallway usability testing, with agents as the users.

Agents doing real investigation work log friction here: what confused them, what
they had to look up, where the results fell short, and what they worked around.
A silent workaround is the most expensive failure mode — it hides a defect *and*
costs the worker the time they spent routing around it.

**Append; never edit someone else's entry.** Rough notes beat polished ones.
Precise complaints beat praise. A clean run is a data point too — say so in one
line.

## A note on the tool's old name

This log predates a rename. Entries below invoke `cascade-search`; the tool is
now `evidence-search`. The commands are left exactly as they were run, because
a field report that has been tidied up is no longer a record of what happened —
substitute the new name when reproducing one.

## This file is public and redacted; raw notes go in `FEEDBACK.local.md`

Write your raw entry to `FEEDBACK.local.md` in your checkout. It is gitignored:
it can name the task, the subject, the ticket and your local paths, and it is
never published. What goes in **this** file (or in a GitHub issue) is the
redacted version: people, investigations, vendors, committees and places
become stable placeholders (`<person-a>`, `<vendor-a>`, `<pac-a>`,
`<county-a>`); the task is described by its kind ("a federal-court docket
watch"), not its ticket name; no local paths, KB ids, ticket or draft names.
Public identifiers that ARE the bug -- a PIID, an API endpoint, a public
government domain -- stay. Redaction is the one edit allowed to someone else's
entry; every entry here was redacted on 2026-09-29.

## A note on the placeholders

Entries logged before this repo went public named real investigative subjects,
because the agents writing them were mid-task. Those are replaced with stable placeholders
— `<vendor-a>`, `<county-b>`, `<person-c>` — consistently, so an entry that traces
one entity across several queries still reads as one entity.

Public contracting identifiers (PIIDs, award ids, dollar figures) are kept as-is:
they are public records, and they are what makes a bug report reproducible. What
is removed is who was being investigated, and the names of private individuals who
appeared incidentally in court dockets.

If you are logging a new entry and the subject does not need naming to make the
bug clear, do not name it. The identifier shape is almost always the load-bearing
part — "a PIID passed to `--detail`" is the bug; whose PIID it was is not.

## Format

```markdown
## YYYY-MM-DD · <kind of task, redacted> · <model>
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

**Correctness status:** seven USAspending figures the tool produced — including
ones that reached near-publication drafts — were recomputed against the primary
API with cascade-search out of the path. All seven confirmed, four to the cent.
See the CORRECTNESS AUDIT entry at the end of this file for scope and limits.
Note what that does and does not cover: everything *else* in this log is agents
reporting on their own runs, which is usability evidence, not correctness
evidence.

### Open items, most valuable first (as of 2026-08-19, post-triage)

> **Staleness warning (2026-09-19).** The line below -- "everything else in this
> file is marked `[fixed]` or `[wontfix]`" -- was true on 2026-08-19 and false
> for a month after it: new entries accumulated beneath it with no triage
> marker, and the same correctness bug was reported twice, weeks apart, because
> nothing marked the first report as known.
>
> Treat this list as the 2026-08-19 open set, not the current one. The
> 2026-09-19 triage pass at the end of this file carries what is open now.

Everything else in this file **as of 2026-08-19** is marked `[fixed]` or
`[wontfix]` inline. These are what was left, and each is here because a
worker's argument for it was good:

1. **`extract` display cap should scale to POST-extraction size.** Truncating a
   survivor that extraction already reduced 98% works against the tool's own
   value proposition. Nearly cost a worker their central finding.
2. **`usaspending --transactions`** — the per-award modification history (date,
   amount, description). Serves this beat's recurring "is this a new contract
   or a bigger bite of an old one" question, which currently requires raw curl.
   (The award `description` half of this request is now fixed and renders.)
3. **Read timeouts are labelled `RateLimited`.** Different cause, different
   retry strategy, and the advertised retry interval is fabricated for them.
4. **`extract --tables --json`** — row arrays for genuinely tabular documents.
   "The corpus is tabular and the tool reads it as prose" is the recurring
   shape of this beat's documents.
5. **Rate-limit contention gives no queue signal.** usaspending is 305 of 552
   logged calls; workers cannot tell brief contention from hopeless.

Known boundary, deliberately not built: **publication-coverage counts**
("how much does site X write about Y"). Both available roads — site-scoped
engine counts and a publication's own tokenising search — return a number that
looks like a census and is not one. Declining beats shipping a plausible fake.

---

## 2026-08-19 · build session · claude-opus-5 · SEED ENTRIES

Found while building and dogfooding. Recorded here as worked examples of the
format, and because the failure modes are the ones worth watching for.

### ProPublica returned 35 identical placeholder rows

**Command:** `cascade-search propublica "<org-g>"`
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
`[fixed 74711f3 — Store.put raises CachePoisoned; replayed results now carry cache_age_s]`

### Two back-to-back commands, second one RateLimited

**Command:** any two sequential calls to the same source
**Expected:** both to run
**Got:** `RateLimited` on the second, with a 0.5s wait
**Friction:** the README's own examples are sequential, so following the docs
produced an error.
**Would have helped:** an opt-in flag to absorb short spacing waits.
**Severity:** annoyed
`[fixed 708fa63 — --wait absorbs spacing only, never a budget window]`

### Could not tell whether a gate was worth solving twice

**Command:** `cascade-search gate list`
**Expected:** a list of distinct things needing a human
**Got:** the same OSCN search parked twice under different tokens, plus two
entries showing `gate_type: None` / `capture: None`
**Friction:** no way to see the two were the same target without reading URLs
closely; "None" where a description belonged.
**Would have helped:** collapsing duplicates, and describing gates in English.
**Severity:** slowed
`[fixed f5ae696 — gate ui dedupes and describes; CLI list unchanged]`

### SearXNG failures misclassified under a non-English locale

**Command:** `cascade-search web "..."` against an instance answering in German
**Expected:** throttled engines reported as rate-limited
**Got:** `zu viele Anfragen` matched nothing and was filed as a hard error
**Friction:** failed safe (coverage still dirty, no false absence) but with the
wrong label — and a wrong label on a retryable condition is how a worker gives
up on something it should have retried.
**Would have helped:** pinning the response locale.
**Severity:** slowed
`[fixed 198de0c — locale=en pinned; unrecognised messages now flagged unclassified]`

---

## 2026-08-19 · eval-1-blocked-is-not-absence · claude-opus-5
**Command:** `cascade-search --wait oscn --county <county-d> --lname <surname> --year 2013`
**Expected:** docket rows for a common surname in the state's largest county, or a clean absence
**Got:** `AwaitingHuman`, exit 4, `coverage: 0/2 responsive | ERRORED: oscn, oscn:browser`, captcha gate token `9fe41fafed61`. Retried once, same wall, new token `a749a449f759`.
**Friction:** Minimal on the core job — this is the tool working exactly as advertised. The typed outcome plus the `0/2 responsive` coverage line made it immediately unambiguous that I had been blocked rather than found nothing. With a plain WebSearch I would have had an empty page and a real temptation to write it up as "no records found." That is the failure the tool exists to prevent, and it prevented it. Worth saying plainly: the single most useful thing here was that exit 4 is not exit 1.
**Had to figure out:**
  1. `oscn --help` does not say which county names are valid, or what format they take. I guessed `<county-d>` from the pattern in the SKILL examples (`<county-b>`). It worked, but I had no way to confirm in advance that `<county-d>` was the right token for <county-d> County rather than, say, `<county-d>-county` or a FIPS code — and a wrong county string would presumably also produce a result I might misread. A `--county` value list, or an error that distinguishes "unknown county" from "county fine, blocked," would remove that doubt.
  2. Nothing in the blocked output tells me whether a gate for this exact URL is ALREADY queued. I had to run `gate list` separately to discover `ebace2555278` — a gate on the byte-identical <county-d>/<surname>/2013 URL from a concurrent run. My two runs then added two more. Three gates for one query is noise for whoever clears them.
**Surprising:** Retrying created a SECOND gate token rather than returning the existing one. Gates look content-addressable in principle (same URL, same capture instruction), so dedupe — or at least "a gate for this URL is already queued as X" — seems like it would be strictly better than accumulating duplicates. `gate list` currently shows 8 gates of which at least 5 are same-URL duplicates (three <county-e>/<surname-2>/2015, two <county-d>/<surname>/2013).
**Also surprising, smaller:** `$CS limits` showed oscn at `5/30 per 3600s` already consumed before I ran anything, which was genuinely useful context — I could see the concurrent run's footprint. But the oscn note says "Turnstile after ~10 fetches/session" and I hit Turnstile at what the ledger showed as fetch 6. Either the ~10 estimate is optimistic, or session-scoped and ledger-scoped counts are different things and the note does not say which.
**Would have helped:**
  - A one-line hint in the blocked output when an equivalent gate is already pending.
  - `--county` accepting/validating against a known list, or `oscn counties` to enumerate.
  - Clarifying in the oscn note whether the "~10 fetches/session" threshold relates to the shared-ledger count that `limits` reports.
**Where it saved real work:** The `coverage: 0/2 responsive` line did in one glance what would otherwise have been several minutes of manually poking the URL to work out whether OSCN was down, slow, empty, or walling me. And the exit-code contract meant I did not have to reason about whether my negative was trustworthy — the tool refused to let me construct one. For a "can I publish this negative?" question specifically, this is the right shape.
**Severity:** annoyed (gate duplication, county-name guessing) — the core outcome-typing worked and was the point.
`[fixed b3bc6be — gates dedupe at creation (same URL supersedes rather than accumulates); --county validates against the 77-county list and --list-counties enumerates them]`
`[tracked — the oscn note's "~10 fetches/session" still does not say whether it means the shared-ledger count that `limits` reports. Observed Turnstile at ledger-fetch 6.]`

## 2026-08-19 · eval-0-verified-absence-is-publishable · claude-opus-5
**Task:** does Bartholomew Quillfeather have a <county-b> County OK court record from 2013

**Where it saved me real work — the control-query trick worked because the tool made it cheap.**
OSCN gated on my target query. I had no way to know from one gate whether the
wall was systemic or somehow specific to a nonexistent name, so I re-ran with
`--lname Smith` in the same county/year. Identical captcha gate. That took one
command and settled it. With a plain scraper I'd have gotten an empty results
page for Quillfeather and very likely written "no record found." This is the
exact failure the typed outcomes exist to prevent, and it fired on the first
real task I pointed it at. `gate list` showing 7 gates across three counties
sealed it, and `limits` proving oscn was at 5/30 (not budget-blown) ruled out
the other explanation. Three commands, no guessing.

**Friction 1 — `extract` returned AccessBlocker but exited 0. Severity: blocked (briefly).**
**Command:** `cascade-search extract "https://ace-usa.org/blog/role-type/alumni/test-bartholomew-quillfeather-george-mason-university/" --text`
**Got:** `== AccessBlocker ==` / `mechanism: http-403` / `NOT a negative finding. Access was blocked.` — and then `EXIT:0`.
**Expected:** exit 2. The SKILL.md table says AccessBlocker = exit 2, and the
whole selling point is that the exit code carries the outcome. I was running
`echo "EXIT:$?"` after every call and had started trusting the code over the
banner text. If I'd been branching on exit status in a loop — which the skill's
own example snippet encourages — I would have filed a 403 as a successful fetch
and possibly quoted an empty extraction. The banner and the exit code disagreeing
is worse than either being wrong alone, because it teaches you to distrust the
mechanism the tool is built around. Unclear to me whether this is `extract`
specifically not mapping outcomes to codes, or AccessBlocker generally.

**Friction 2 — `gate list` exits 4. Severity: annoyed.**
`cascade-search gate list` returned exit 4 (AwaitingHuman). I get the logic —
there are gates awaiting a human — but `gate list` is an inspection command that
succeeded at what I asked. Exit 4 on a listing means I can't put it in a script
with `set -e`, and for a moment I thought listing the gates had itself hit a gate.
An inspection subcommand reporting the queue state should exit 0; the queue's
contents are the payload, not the outcome.

**Friction 3 — `oscn --help` doesn't say which counties or what the date semantics are. Severity: annoyed.**
The help is a bare flag list: `--county`, `--lname`, `--fname`, `--year`, `--case`.
I had to guess that `--county <county-b>` is a lowercase bare name (not "<county-b> County",
not a FIPS code) — the SKILL.md example carried me, but the help alone wouldn't
have. I also couldn't tell from help whether `--year 2013` filters on filing date
or disposition date; only the gate URL revealed it expands to
`FiledDateL=1/1/2013&FiledDateH=12/31/2013`. That's filing date, which is usually
what you want, but it's a load-bearing assumption for a publishable negative and I
had to reverse-engineer it from a failure message. One line per flag would fix it.

**Friction 4 — `web` name searches collapse to near-miss tokens with no way to force exact-phrase. Severity: slowed.**
**Command:** `cascade-search --wait web "Bartholomew Quillfeather"`
**Got:** 29 results, ~25 of them about "Bartholomew Quill," a children's book
about a crow, plus pages matching "Bartholomew" and "feather" separately (quill
pens, Bartholomew the Apostle, a Wikipedia article on pens).
**Friction:** I wanted "this exact string or nothing." I tried the quoted form —
which is what the SKILL.md news example uses (`'"<vendor-a>" interdiction'`) —
but at the `web` layer the quotes appear to be consumed as shell quoting rather
than passed through as a phrase operator, and I couldn't tell which. There's no
`--exact` or `--phrase` flag. On a distinctive-surname query this is high-cost:
the one genuinely relevant result was ranked #10 under nine crow-book pages, and
if I'd trusted the top of the list I'd have concluded something quite wrong.
**Would have helped:** either an `--exact` flag, or a note in the output when the
engines fuzzed the query — "matched on partial tokens" as a coverage-style line.

**Friction 5 — no obvious way to ask "was this whole source walled today?" Severity: cosmetic.**
I inferred OSCN was globally gated by running a control query and then reading
`gate list` for other people's stale tokens. That worked, but it's detective work.
Something like `cascade-search limits --health` or a per-source "last successful
fetch" timestamp would have answered in one call what took three plus interpretation.

**Small positive worth recording:** `*UNIQUE*` did its job. The single exact-name
hit ("TEST Bartholomew Quillfeather, George Mason University" — a CMS placeholder
record) was marked UNIQUE at rank 10. That marker is what made me look at it
instead of stopping at the crow book, and it turned out to be the most
interesting fact in the whole search: the name may be a test fixture, not a
person. Consensus ranking would have buried it and I'd have missed it.

## 2026-08-19 · eval-2 general-web · claude-opus-5 (research-search skill)

Task: find recent reporting on ICE warehouse detention facilities and their contractors,
return 3-4 strongest sources with URLs. Ran ~11 cascade-search calls, no WebSearch fallback
needed. Overall the tool did the job well. Specific friction below.

**1. `news` returns Google News redirect URLs, which are useless as citations.**
**Command:** `cascade-search --wait news 'ICE warehouse detention contractor'`
**Expected:** publisher URLs I can cite.
**Got:** 77 results, every single URL of the form
`https://news.google.com/rss/articles/CBMizwFBVV95cUxQaXFDRlBlVkluc2QtVjN0Ti0yYVJUOEZ1ai1OLThrcHp2...?oc=5`
— 300+ character opaque base64 redirects. The titles carry an em-dash publisher suffix
("... - The Washington Post"), so I could tell WHO published it but not WHERE it lives.
**Friction:** the news pass was effectively unusable for the deliverable. I got real URLs only
because the `web` pass had already surfaced most of the same stories with clean links. If the
question had been news-only, I'd have had to hand-resolve dozens of redirects.
**Had to figure out:** that I should just lean on `web` results and treat `news` as a
title-level "what exists" scan rather than a source of citations.
**Would have helped:** resolve the redirect (one HEAD request follows it) and emit the
publisher URL, or emit both. Also a `publisher:` field instead of gluing it into the title.
**Severity:** slowed — arguably blocked for a news-only task.
`[fixed 81dc1a1 — redirect URLs are marked non-citable and the publisher is surfaced from the feed rather than glued into the title. The redirect is NOT resolved: following it per-result spends a fetch apiece and Google rate-limits the hop. `news` is a "what exists" scan by design; `web` is the citation path.]`

**2. `extract --ids` silently over-filters and reports a misleading reduction number.**
**Command:** `cascade-search extract https://<outlet-f-domain>/p/<post-slug> --ids`
**Expected:** the contract dollar figures and contract/modification IDs from an article whose
literal headline is "$313 Million Contract".
**Got:**
```
raw ~49,557 tok -> extracted ~10 tok (100.0% reduction)
  money          $70,035,000
```
One dollar figure — and not the headline one. `$313 million`, `$113 million`, and the
modification IDs `P00001` / `P00002` are all in the body and all missed. My read: the money
regex only matches digit-grouped `$70,035,000` form and not `$313 million` prose form, and
`P00001` isn't in the ID vocabulary.
**Friction:** this is the dangerous failure mode, because a 100.0% reduction with one clean
row LOOKS like a successful precise extraction. If I hadn't independently known the headline
figure I would have written down $70,035,000 as the contract value and been wrong. The next
worker will not always have that check.
**Had to figure out:** to abandon `--ids` and re-run with `--grep` on contractor names, which
worked fine (95.7% reduction, all the facts).
**Would have helped:** (a) match `$NNN million/billion` prose forms in the money pattern;
(b) add federal contract modification IDs (`P0000N`, `A0000N`) and PIIDs to `--ids`;
(c) when `--ids` yields under ~5 rows on a large document, say so — "1 match; consider --grep"
— rather than reporting a triumphant 100.0%.
**Severity:** slowed, with a real correctness hazard.
`[fixed 1d20154 + 5f46710 — all three sub-items. (a) money matches scaled prose ($313 million, $1.2bn) and trailing-unit forms, not just digit-grouped; (b) contract modification IDs and PIIDs are in the --ids vocabulary; (c) a zero/low-yield extraction no longer reports a triumphant 100.0% reduction — the count is stated and a bare miss says so. This entry named the tool's own worst failure mode (a confident-looking wrong number) and it is the one that got fixed most thoroughly.]`

**3. No way to page a large result set without re-running the whole search.**
I wanted results 15-30 of a 30-result `web` hit. There's no `--offset`, no `--limit`, and the
output goes to stdout as formatted text, so I re-ran the identical query and piped through
`sed -n '55,120p'`. Second run was served fast (cache, presumably) but it still spends a
rate-limit slot on a query I already had.
**Would have helped:** `--limit/--offset`, or just say "use `--json | jq`" in the help text.
I know `--json` exists from the skill doc but it wasn't obvious it was the paging answer.
**Severity:** annoyed.
`[fixed ec88f4c — --limit is now on every listing source, spelled the same way. No --offset: paging a cached result set belongs in `--json | jq`, and adding an offset that silently re-runs the query would spend a rate-limit slot to look like paging.]`

**4. Where it clearly earned its keep — three things.**
- `AccessBlocker` on the TIME article did exactly what the pitch says. Exit 2, `mechanism:
  recaptcha`, `HTTP 200`, plus "NOT a negative finding" and "ESCALATABLE". The HTTP 200 is the
  detail that matters: a naive fetch would have returned a 200 with a challenge page and I'd
  have had no idea I was reading a wall instead of an article. I wrote it into the answer as
  an explicit caveat instead of silently dropping the source.
- The coverage line let me characterise my own confidence honestly. `80/82 responsive |
  RATE-LIMITED: brave | ERRORED: startpage` on every web pass — good sweep, two named gaps, so
  I noted in the answer that no negative from this session would be publishable. That sentence
  does not exist without the coverage line.
- `extract --grep` is the actual workhorse. Four extractions, 92.7%-95.7% reduction each,
  pulling exact quoted contract language ("THIS MODIFICATION IMPLEMENTS A STOP WORK ORDER",
  "Where the ICE Detention Standards are silent, the ACA Jail Standards must be followed") that
  I could quote with confidence because it's DOM traversal and not a model paraphrase. Roughly
  170k raw tokens of source read for about 9k. That's the feature.
- `*UNIQUE*` markers were genuinely predictive on this beat. <outlet-f> — a small
  Substack doing the best USASpending.gov modification-record reporting on this story, better
  than the majors on procurement mechanics — was UNIQUE-flagged and would have been buried by
  consensus ranking. Two of my four headline sources came from UNIQUE hits.

**5. Small thing:** `--wait` worked as documented across sequential calls; zero spurious
`RateLimited`. Nothing to report there, which is itself the point.

## 2026-08-19 · FOIA records task (a federal employee's tenure) · agent:claude-sonnet-5-parallel-tick1-c

**[tracked]** Web/SearXNG rate-limiting cluster, the most-reported friction in this log. Partly root-caused 2026-08-30: the SearXNG daemon was running against a deleted settings path, and four default general engines are durably blocked. Re-reports add corroboration, not new signal.

**Commands:** `cascade-search --wait web '"<person-h>" CBP FOIA'`, several follow-up `--wait web`/`--wait news` variants, `courtlistener "<person-h>" --type r`, `propublica "<person-h-surname>"`, `extract https://www.cbp.gov/site-policy-notices/foia/records`
**Expected:** either a hit connecting a named person to a federal LE role, or a clean VerifiedAbsence/AccessBlocker split for a low-traffic, low-search-volume subject.
**Got:** mostly worked as documented. One recurring friction: intermittent `RateLimited` on `brave` even when `cascade-search limits` immediately after showed `brave usage: 0/20 per 1s` — i.e. the limits output didn't explain why the prior call was rate-limited. Simple retry after ~15s cleared it. Plausibly another concurrent worker (this is a multi-agent parallel-tick session) burning the shared brave/searxng budget between my `limits` check and the next call — if so this is expected/correct behavior, but the `limits` snapshot reads as "budget available" right after a `RateLimited` response, which is momentarily confusing for a solo user trying to self-diagnose vs. a shared-ledger race. A "last rate-limit event" timestamp or brief note in the RateLimited output ("shared ledger, another caller likely consumed budget between calls") would have made the diagnosis immediate instead of inferred.
**Worked well:** the `VerifiedAbsence` on `--wait news '"<person-h>" Border Patrol'` was exactly the right primitive for this task — a person-identity question where the deliverable is "confirm this genuinely isn't findable in the news index," and the typed outcome let me write that up as a clean negative with a one-line justification instead of hedging.
**Worked well:** `extract --grep`/plain `extract` on the CBP FOIA-process page was excellent for pulling the one operative fact (SecureRelease-only intake, effective date, routing table) out of a page that was otherwise mostly nav chrome — did not need to eyeball the raw page at all.
**Severity:** annoyed (rate-limit diagnosis friction only; no blocked work — retry resolved it in under 20s).

## 2026-08-19 · subaward-records verification task (legal-services provider) · agent:claude-sonnet-5-parallel-tick1-b

**[tracked]** Browser allow-list gap, the second most-reported friction. Root cause addressed 2026-09-19: the list was hardcoded with no override, so every registry needed a commit. It is now extendable via `~/.evidence-search/allowed_hosts.json` with a required reason; run `evidence-search hosts`. The specific host in this entry still needs adding, but you can now add it yourself.

**Commands attempted:** `cascade-search docs "<org-b> legal services cooperative agreement" --site federalregister.gov`; also `cascade-search limits`.
**What happened:** the task needed federal-procurement-database verification (USAspending awards/subawards/transactions, Federal Register full-text search, FPDS-NG). None of these are `cascade-search` first-class sources — the closest fit, `docs`, is scoped to *vendor documentation* (`claude-code`/`claude-api` sites only per its own error message: "unknown docs site 'federalregister.gov'; known: claude-code, claude-api") and returned a `VerifiedAbsence` that was actually just "wrong tool for this corpus," not a real negative about federalregister.gov content. I nearly wrote that up as a publishable negative before re-reading the coverage line and realizing it meant "I don't know this site," not "this site has nothing."
**Worked around it:** dropped to raw `curl` against USAspending's public JSON API (`api.usaspending.gov/api/v2/awards/`, `/transactions/`, `/subawards/` via POST) and the Federal Register's own documented-ungated JSON API (`federalregister.gov/api/v1/documents.json`) directly — both worked fine unauthenticated, no rate limits hit, no CAPTCHA. Also hit a genuine SAM.gov/FPDS-NG session-gate (`406 Not Acceptable` from `sam.gov/api/prod/sgs/v1/search/` without an authenticated session) that I documented as an AccessBlocker by hand, since there's no cascade-search client for SAM.gov/FPDS-NG at all.
**Gap worth flagging:** this is the second-largest procurement/contracts beat in the corpus (federal contract awards, subawards, FPDS ceilings) and cascade-search has no dedicated USAspending or Federal Register client, only the generic `docs` command whose site allowlist is vendor-docs-only. A `cascade-search usaspending` (award/transaction/subaward lookups by PIID/FAIN/recipient) and a `cascade-search fedreg` (full-text search, the API is already confirmed ungated per a prior pass's method notes) would both be straightforward wins on this beat — same shape as the existing `oscn`/`courtlistener`/`propublica` clients, same typed-outcome benefit (right now a raw `curl` against USAspending gives no `Hit`/`VerifiedAbsence`/`AccessBlocker` distinction; I had to reason about "reporting lag vs. true absence" entirely by hand).
**Worked well once I dropped to curl:** USAspending's API is fast, unauthenticated, and well-structured — `spending_by_award` (POST, recipient_search_text + award_type_codes + time_period), `/awards/{id}/`, `/transactions/` (POST, award_id, sorted by action_date), `/subawards/` (POST, award_id) all returned clean JSON with no friction. If a client gets built, that's the shape to mirror.
**Severity:** annoyed, not blocked — the `docs --site` error message was actually good (named its own allowlist), which is what stopped me from mis-writing the VerifiedAbsence. But the absence of a procurement-data client on a research beat this procurement-heavy is a real coverage gap, not just a one-off.

## 2026-08-19 · CourtListener industry-scale count task · agent:claude-sonnet-5-parallel-tick1-d

**[tracked]** Web/SearXNG rate-limiting cluster, the most-reported friction in this log. Partly root-caused 2026-08-30: the SearXNG daemon was running against a deleted settings path, and four default general engines are durably blocked. Re-reports add corroboration, not new signal.

**Commands:** `cascade-search limits`; `cascade-search courtlistener "<vendor-b>" --type r`; `courtlistener '"<vendor-b-full>"' --type r`; `courtlistener "<vendor-c>" --type r`; `courtlistener '"<person-b>"' --type r`; `courtlistener '"<vendor-b-full>"' --type o`; `courtlistener "<vendor-c>" --type o`; `courtlistener "<person-c>" --type o`; `--wait` variants of most of the above; `extract https://www.<vendor-c-domain>/about.html --grep ...`; `extract https://www.<vendor-b-domain>/about/ --grep ...`; several `WebSearch` calls.

**1. The tool discards the one number this task most needed, and that's a real gap, not just my bad luck.** `cascade_search/sources/courtlistener.py`'s `_parse()` only reads `data.get("results", [])` from the CourtListener search API response — it never reads the top-level `count` field, which CourtListener's API does return and which is exactly "how many total matches" — the deliverable this task was scoped to produce. The CLI additionally caps display at whatever page size the API defaults to (20 rows). So every query that filled the page (`"<vendor-b-full>"` type=r, `"<vendor-c>"` type=r, both 20/20) had to be written up as "at least 20, true total unknown" rather than a real count — the single biggest quantitative gap in my final writeup exists because of this, not because the data isn't there. A one-line change (surface `data.get("count")` into the Coverage or a Result-level meta field) would close it. I did not attempt to page past row 20 or hit the API directly myself, since that would have meant either bypassing the shared rate-limiter (bad) or spending more of the shared daily budget chasing pagination by hand for a number the client should just report.

**2. `--wait` did not block through short (1-25s) rate-limit windows on courtlistener, contrary to what its own help text and the skill doc say ("blocks for short spacing waits instead of returning RateLimited (sub-minute only...)").** I hit the 5/min courtlistener window three times in a row; each time `--wait` returned `RateLimited` immediately (exit 3) with a `retry after: Ns` value under 30s, rather than sleeping and retrying. I had to manually re-issue the same command a turn later once the window had naturally ticked down. This is the opposite of a prior worker's note in this file ("`--wait` worked as documented across sequential calls; zero spurious RateLimited") — possibly the difference is per-source (that prior note was about `brave`/`web`, mine is specifically `courtlistener`), or possibly courtlistener's stricter concurrent 5/min-and-50/hr-and-125/day combination interacts badly with the wait logic (maybe it computes a wait against the wrong window, or refuses to wait when ANY of the three windows — not just the tightest sub-minute one — is exhausted). Worth a source-specific look.

**3. Query-construction discipline that worked, and its inverse that didn't, both worth encoding as guidance.** Exact-phrase institutional-name queries (`"<vendor-b-full>"`, `<vendor-c>` as a standalone acronym) returned clean, overwhelmingly on-topic result sets — I could eyeball 20/20 case names and immediately recognize the excessive-force/§1983 pattern. Personal-name queries (`"<person-b>"`, `"<person-c>"`) returned heavily diluted result sets — common surnames pull in unrelated bankruptcy, contract, and criminal cases with no connection to the subject, and CourtListener's full-text search gives no way to disambiguate without opening each docket. I discarded both personal-name query results rather than risk citing tokenizer noise as signal. This seems like a generally useful principle for `references/sources.md`: prefer institutional/proper-noun-phrase queries over personal-name queries on CourtListener whenever the person has a common surname, and treat 20/20-full personal-name results as inconclusive until spot-checked.

**4. Where it earned its keep.** The `type=o` (published opinions) vs `type=r` (RECAP dockets) distinction turned out to be the single most useful lever in this session — opinions are a much smaller, cleaner, and higher-confidence corpus (3 results for "<vendor-b-full>", well under the page cap, so very likely a near-complete count) than dockets (capped at 20, true total unknown). That distinction let me report two genuinely different numbers with different confidence levels instead of collapsing them into one misleading figure. `extract --grep` on <vendor-c-domain> and <vendor-b-domain> also worked cleanly and confirmed both domains **no longer 403-block automated fetch**, reversing a documented blocker from an earlier pass in this same research thread — worth a note that "previously blocked" should be periodically re-tested, not treated as permanent.

**Severity:** annoyed, not blocked. Worked around the count-discarding gap by explicitly labeling every number's tier/confidence in the writeup; worked around the `--wait` gap by manual retry.

## 2026-08-19 · reporting-leads task (affected-party voices) · claude-sonnet-5-parallel-tick1-a

**[tracked]** Web/SearXNG rate-limiting cluster, the most-reported friction in this log. Partly root-caused 2026-08-30: the SearXNG daemon was running against a deleted settings path, and four default general engines are durably blocked. Re-reports add corroboration, not new signal.

**Command:** `cascade-search extract <local-court-complaint>.pdf --grep "Plaintiff" --grep "declares" ...` and later `--text`
**Expected:** matching passages (grep mode) or clean chrome-stripped text (--text mode) from a local court-filing PDF already archived in the research notes.
**Got:** grep mode returned PDF *object-stream* noise — `/D [34 0 R /XYZ 84 588 0] /S /GoTo >> endobj 717 0 obj <<...` — instead of the document's actual paragraph text, even though the matched keyword ("Plaintiff") does appear literally in that noise (as part of PDF outline/bookmark titles) as well as in the real body text. `--text` mode was worse: it dumped raw PDF binary structure (xref tables, XMP metadata streams, font dictionaries) with a NEGATIVE reported reduction (-224.2%, i.e. the "extracted" output was over 3x the raw token estimate), never reaching the actual page-content operators.
**Friction:** could not tell from the tool's own output whether this was a "this PDF doesn't have an extractable text layer" case or a general extractor bug — there's no signal distinguishing "here is real matched content" from "here is PDF-syntax noise that happens to contain your grep string." I burned a few minutes reading extracted "text" before recognizing it was PDF structure, not body content. This specific document is a court complaint with a normal text layer (confirmed below) — not a scanned/OCR case where a failure would be expected.
**Had to figure out:** dropped to plain `pdftotext -layout` (Homebrew poppler, already on PATH) instead, which extracted cleanly (2,143 lines, real paragraph text, correct plaintiff-organization list with sworn caseload figures) on the same file. So the PDF is fine; cascade-search's extractor specifically mishandled it. I didn't dig into why (Aspose-produced PDF per its metadata — /Producer "Aspose.PDF for .NET 24.2.0 ... modified using iText Core" — possibly an object-stream/cross-reference-stream structure the extractor's parser doesn't walk correctly), just worked around it.
**Would have helped:** (1) a sanity check in the tool itself — if extracted text ratio vs. raw is negative or the output is mostly non-printable/PDF-syntax tokens, warn rather than silently return it as if it were prose; (2) grep mode specifically matching within already-extracted text rather than raw bytes, so PDF-structure false-positives on common words like "Plaintiff" (which legitimately appears in bookmark titles) don't leak through as if they were body-text hits.
**Where it saved real work elsewhere in this task:** `extract --grep` on live news articles (a state news site, a national daily, NPR) worked exactly as advertised — 96-98% token reduction, clean quote-bearing passages, correctly attributed to speakers. The problem was specific to this locally-archived PDF, not the tool generally. `web` search coverage lines and the Hit/RateLimited typed-outcome distinction were both clear and useful — caught one genuine rate-limit (WaPo, on `extract`) that I correctly did NOT write up as a verified absence.
**Severity:** slowed (a few minutes; had a working fallback immediately available in `pdftotext`)

## 2026-08-19 · synthesis task (separating two contract vehicles) · claude-opus-4-8-parallel-tick1-e

**[tracked]** Web/SearXNG rate-limiting cluster, the most-reported friction in this log. Partly root-caused 2026-08-30: the SearXNG daemon was running against a deleted settings path, and four default general engines are durably blocked. Re-reports add corroboration, not new signal.

Synthesis-heavy task (argue a structural claim from committed artifacts, not lookup). Reporting
specifically on whether the tool supports SYNTHESIS, since that was asked.

**Verdict up front:** `web` and `extract` were excellent and did real analytical work for me.
`courtlistener` was the weakest link and I ended up bypassing it with raw `curl` for the thing
that mattered most. The single most load-bearing evidence in this task — federal procurement
codes — has no cascade-search client at all, and I had to hand-roll USAspending API calls.

### Where it saved me real work (positive signal, be specific)

- **The browser tier is the standout feature and it is undersold in the skill doc.**
  `extract https://www.<org-h-domain>/... --grep "<vendor-b>|<vendor-c>|..."` sailed straight
  through a Cloudflare wall that had just returned me a bare "Just a moment..." interstitial
  on `curl`. I did not ask for escalation, did not know it had happened, and got clean text.
  That silently turned a dead end into the census evidence for half my argument. **Suggestion:
  say in the output when the browser tier was used.** I only inferred it because I'd watched
  plain curl fail on the same URL 60 seconds earlier.
- `web` coverage lines let me trust a thin result set. `"<article-title>" "<vendor-b>" <journal-a> 2025` returned 18 results on
  `79/82 responsive | RATE-LIMITED: brave | ERRORED: duckduckgo, startpage` — I could tell that
  was a real hit on a near-complete sweep, not luck. That query closed a gap ("<person-n>
  could not be located") that two prior passes had logged as unresolved.
- `*UNIQUE*` earned its keep. The Justia hit on the <case-b> Daubert order was UNIQUE, and it was
  the thread that led to the single most important document in the whole task. Consensus
  ranking would have buried a district-court docket page.
- `extract --grep` on the <person-n> CV: 164K tokens of PDF down to a 60-line list of retention
  lines. That is genuinely a synthesis tool, not just a token-saver — the *shape* of the
  grep output (57 "Retained by plaintiff" vs 11 "Retained by defendant") WAS the finding.
  I did not have to read the CV to see the asymmetry; the extraction surfaced it.

### Where it fell short

**1. `extract --grep` works on PDFs; `extract --text` and bare `extract` do not. BLOCKED me briefly.**
Same URL, three behaviours:
```
$CS extract .../<person-n>.pdf --grep "<vendor-b>|expert|..."
  -> raw ~164,707 tok -> extracted ~3 tok (100.0% reduction)     # THREE TOKENS. Silent no-op.
$CS extract .../<person-n>.pdf --text
  -> raw ~164,707 tok -> extracted ~452,468 tok (-174.7% reduction)  # dumped raw PDF binary
     %PDF-1.7 / stream / x���n�F�]���O�0����"֒��(V,el��...
```
A **negative reduction percentage** and a screenful of mojibake is the tool telling me it has
no PDF text layer and is passing bytes through. It should say "this is a PDF, no text
extractor available" and exit non-zero, not print `-174.7% reduction` as if that were a
result. And the `--grep` variant returning "3 tokens" with exit 0 is worse — that is
indistinguishable from "searched properly, found nothing," which is exactly the failure mode
this tool exists to prevent. **A typed outcome is the whole value proposition here and PDFs
silently break it.** I worked around it with `curl -o file.pdf && pdftotext`, which took two
minutes and worked perfectly, so the fix is presumably just shelling out to pdftotext.

**2. `courtlistener` OR-tokenizes unquoted phrases and gives no hint that it did.**
`$CS courtlistener "<vendor-b-full>" --type o` returned 20 results, every one
`*UNIQUE*`, topped by **an unrelated planetary-research institute**, **an unrelated forensic-lab
case**, and **a foreign research institute**. Zero relevant. The shell quotes are
consumed by the shell, so the API sees bare tokens. I had to figure out that
`'"<vendor-b>"'` (nested quotes) was the correct form. **A three-word query returning
an unrelated foreign research institute should trip a "did you mean an exact phrase?" hint**, or the client should
phrase-quote multi-word queries by default.

**3. I abandoned the `courtlistener` client entirely for the task's key document.**
Once I had the docket, I wanted the actual order text. The client gives me search results,
not documents. I went to `curl https://www.courtlistener.com/api/rest/v4/search/?q=...&type=r`
directly, read `recap_documents[].filepath_local`, and pulled
`storage.courtlistener.com/recap/<court>.<docket-id>/<court>.<docket-id>.89.0.pdf`.
That 37-page order is the best evidence in my writeup. **Feature request: `$CS courtlistener
--docket <id> --fetch-documents`, or at minimum surface `filepath_local` / `is_available`
in the result rows.** The RECAP PDF endpoint is ungated and free — the client is one hop
away from being a primary-document retriever instead of a search box, and that hop is the
difference between "I found a case" and "I read the holding."

Also: the top-level `count` field is discarded (another worker logged this same tick). I hit
it too — `results: 20` on a capped page tells me nothing about volume.

**4. No procurement source. This is the biggest gap for this beat.**
The core of my answer is that <vendor-b>'s 21 federal awards split 19-to-2 across
`PSC U005/U008/U009/U010/U013/U099 (Education/Training)` vs `PSC R424 (EXPERT WITNESS) /
NAICS 541199 (Legal Services)`. **cascade-search has no USAspending or FPDS client**, so I
wrote raw `curl` POSTs against `api.usaspending.gov/api/v2/search/spending_by_award/`,
`/awards/{generated_internal_id}/`, and `/search/spending_over_time/`, plus a Python loop to
fan out per-award detail calls for PSC/NAICS. That is ~40 lines of glue I had to get right,
including guessing the `generated_internal_id` format (`CONT_AWD_<piid>_<subtier>_-NONE-_-NONE-`)
— my first attempt used the wrong subtier code and 404'd. The skill doc lists `propublica`
for disclosures and `oscn`/`courtlistener` for courts; on a follow-the-money beat, **USAspending
is at least as load-bearing as either**, it is keyless and generous, and it would benefit
enormously from the typed-outcome treatment. Strongest single feature request in this entry.

**5. Crossref is the workaround for the entire academic-publisher wall, and it should be a source.**
SAGE (`journals.sagepub.com`) is Cloudflare-managed-challenge on both the DOI landing page and
the `/doi/full/` variant. The browser tier did NOT pass it (unlike <org-h>). Justia was also
`cloudflare-managed-challenge`. But `curl api.crossref.org/works/<doi>` returned the full
verbatim abstract, author list with affiliations (including `<person-o>@<vendor-b-domain>`,
which was itself a finding), journal, volume, issue, and pagination — for free, no key, no
wall. I verified two tier-1 citations that way, one of which closed a two-pass-old gap.
**`$CS crossref <doi-or-title>` would be maybe 30 lines and would defuse most paywalled-journal
blockers on this beat.** Right now the tool reports AccessBlocker on SAGE and stops; the
metadata was sitting in the open the whole time.

### On whether it supports SYNTHESIS specifically

Partly, and the part that works is `extract --grep` with multiple alternation patterns over a
long document. Being able to ask "show me every line matching `Retained by plaintiff|Retained
by defendant`" and read the *ratio* off the output is analysis, not lookup, and it cost me
almost no context.

What it does NOT support is **comparing structured records across a set**. My central claim
required knowing the PSC and NAICS code of each of 21 awards and noticing they cluster in two
groups. There is no cascade-search idiom for that; `--tables` is close in spirit but only works
on a single page. I hand-rolled it. **A "fan out this lookup over these N ids and tabulate
these fields" primitive is what synthesis work actually needs**, and it is a different shape
from anything currently offered.

Minor: I twice wanted to know what a *previous* worker had already searched this tick so I
wouldn't burn shared CourtListener budget re-running it. `$CS limits` shows usage numbers but
not what was queried. A recent-query log against the shared ledger would have saved me two
calls and, more usefully, told me a sibling had already covered another agency's award stream — I only
found that out by reading the research notes afterwards.

**Severity:** slowed (PDF extract, courtlistener quoting/documents), annoyed (no query log),
and one genuine capability gap (no procurement client, no crossref) that I fully worked around
but which cost the most time.

## 2026-08-19 · <vendor-b>-analyst RECAP census · agent:claude-opus-5
**Commands:** `cascade-search --wait --no-cache --json courtlistener '"<vendor-b> Analyst"' --type r` and five phrase variants; `cascade-search limits`
**Worked well — the `total_matches` fix landed and immediately changed a finding.** My task body was written EARLIER THIS AFTERNOON, by a worker in this same session, and explicitly told me to bypass the client and call the API directly "because cascade-search's courtlistener client discards the top-level 'count' field and caps display at 20 rows, so every number is a floor." It was fixed a few hours later, in the same session, and the client gave me 85 directly. The whole loop -- worker hits the gap, logs it, maintainer fixes it, next worker benefits -- closed inside a single afternoon. The whole point of this ticket was producing a scale figure the company-census approach could not; without `count` the honest answer would have been "at least 20," which is the number that made four prior passes stall.
**Worked well — RateLimited typing.** Two of six queries came back `RateLimited` mid-pass. Because it is a distinct outcome rather than an empty list, I waited for the window and retried instead of recording a smaller count. On a counting task specifically, a rate-limited query silently treated as zero would have corrupted the headline number.
**Friction 1 — `--type r` returns empty `url` fields. Severity: slowed.** Every RECAP result had `url: ""`, so I could not link or fetch a case from search output. `absolute_url` is null on RECAP rows in the upstream API too, so this may be faithful rather than a client bug — but the client could construct `courtlistener.com/docket/<docket_id>/` from `docket_id`, which it already receives and also discards. I had to call the API directly to get `docket_id` and verify a case existed at all.
**Friction 2 — the client drops `recap_documents` and `attorney`. Severity: slowed.** These are where the actual evidence lives: `recap_documents[].description` is what let me classify 75/96 documents as expert-disclosure-shaped, and `attorney` would have answered the retaining-party question directly instead of me inferring it from case captions. I fell back to `httpx` for both, which meant hand-rolling the pagination and the 5/min pacing that the client would otherwise have handled.
**Would have helped:** surface `docket_id` (and a constructed docket URL), `recap_documents[].description`, and `attorney` in `Result.meta`. Those three fields would have kept this entire task inside the client.
**Note on the quoting warning:** the new CLI help text (`QUOTE phrases when counting`) is well-placed — I hit the exact case it describes. Unquoted returns 51,622, quoted returns 155. I would not have caught that on my own before writing the number down.
**Severity:** slowed (two fields-missing detours; no wrong output, and the core fix materially improved the finding).

## 2026-08-19 · FOIA J&A task (nonprofit and law-firm awards) · agent:claude-sonnet-5-parallel-tick2-c
**Task:** determine whether an HHS FAR 6.302-2 urgency J&A is publicly available or genuinely FOIA-only, and draft the request if not. Explicitly asked to stress-test `fedreg` and `usaspending`, both hours old at the time.

**Worked well — `fedreg --type notice` gave a trustworthy negative.** `$CS --wait fedreg "<org-a>" --type notice` returned 20 real results with a real corpus behind them, and none matched — which let me write "no FR notice exists for this J&A" as a claim I could stand behind, rather than "I didn't find one." That distinction is the entire point of the tool and it held up under an actual use case (deciding to file a FOIA request rather than keep hunting).

**Worked well — `usaspending "<org-a>" --count` was exact and fast.** One call, one award, matched the research notes' prior finding. No friction; this is the easy case (recipient-name match, not keyword).

**Friction 1 — `fedreg` phrase matching is not exact, and this matters for a J&A search specifically. Severity: annoyed, borderline slowed.** `fedreg "<org-a>" --type notice` did NOT restrict to documents containing that phrase — it returned 20 unrelated agency-information-collection notices (NOAA, FAA, FCC Unified Agenda, arms sales) with no visible connection to "<org-a>" in the title or snippet shown. I could not tell from the output alone whether this was a true zero (tokenized on the name's first word as a stopword-adjacent term, similar to the courtlistener unquoted-phrase problem documented elsewhere in this log) or a real ranked-but-irrelevant result set. I had to reason from FAR-authority knowledge (urgency J&As for *contracts*, unlike cooperative-agreement single-source-intent notices, don't require FR pre-publication) to conclude the emptiness was structurally expected rather than a tool miss — the tool itself gave me no signal either way. **Would have helped:** either (a) `fedreg` support a quoted-phrase mode with the same "quote it or your count is off by orders of magnitude" warning that `courtlistener` now carries, or (b) the output flag when zero of the returned rows contain the literal query string, so a worker doesn't have to eyeball 20 titles to notice none of them are about the subject at all.

**Friction 2 — no client for procurement-file attachments (GovTribe/HigherGov/SAM.gov contract-file documents). Severity: slowed, worked around.** The actual J&A, if posted anywhere short of FOIA, would live as a PDF attachment on a procurement aggregator or SAM.gov itself — `usaspending` and `fedreg` structurally cannot see this (neither indexes contract-file attachments). I fell back to `web`, which found the right aggregator listing pages via snippet text, but `extract` on GovTribe hit `AccessBlocker` (Cloudflare managed challenge) and `extract` on HigherGov returned 0 usable tokens (JS-rendered contract page, no matching text in the DOM). Neither is a `cascade-search` bug — both are genuinely hard targets — but it means "is there a J&A attachment on this specific award" is currently unanswerable by any typed-outcome path; I had to reason from the *absence of any hit* across three different search angles plus a same-office aggregator precedent (GovTribe does host J&As for *other* HHS/OMAS awards) to reach a defensible "genuinely FOIA-only" conclusion, rather than getting there from one tool call.

**Worked well — `AccessBlocker` on GovTribe was unambiguous and let me log a gap instead of a false negative.** Without the typed outcome I might have silently treated "extract returned nothing useful" the same for both the Cloudflare wall (GovTribe) and the JS-render miss (HigherGov) — but the explicit `AccessBlocker` on one and the visible 0-token/100% reduction on the other let me describe them differently in the work log (one is a real gap, the other is closer to a true empty match).

**Severity:** slowed overall — no wrong output produced, but reaching a defensible "FOIA-only" conclusion took four search angles plus outside FAR-process knowledge rather than one clean negative-finding call, because neither `fedreg` nor `usaspending` covers procurement-file *attachments*, which is where a J&A actually lives if it's public at all.

## 2026-08-19 · named-story sourcing task (<vendor-b>/<person-c>) · agent:claude-sonnet-5-parallel-tick2-b

**[tracked]** Web/SearXNG rate-limiting cluster, the most-reported friction in this log. Partly root-caused 2026-08-30: the SearXNG daemon was running against a deleted settings path, and four default general engines are durably blocked. Re-reports add corroboration, not new signal.

**Task:** find a named, tier-1 plaintiff/decedent story from the 155-quoted-phrase CourtListener docket set behind the <vendor-b>/<person-c> credential, following up on the prior worker's aggregate-scale RECAP census.

**Worked well — the quoted-phrase discipline from the skill doc was exactly right and I didn't have to rediscover it.** `courtlistener '"<decedent>" "<vendor-b>"' --type r` went straight to the one relevant docket on the first call because I quoted both phrases from the start; no tokenizer-noise detour this pass.

**Worked well — `meta.party` and `meta.recap_documents[].description` on a docket-search hit were exactly what closed this task.** The `--json courtlistener` hit for "Estate of <decedent> v. <municipality>" returned `party: ["<next-of-kin-1>", "<next-of-kin-2>", "Estate of <decedent>", "<decedent>, Jr.", ...]` and a `recap_documents` entry titled "TRIAL BRIEF Re: Defense Expert <person-i> and His Reliance on Unreliable Principles of <person-c> and <vendor-b-full>" in the same call. That single JSON response gave me the decedent's family names AND the exact document that ties the doctrine to the case, with no second query needed. This directly contradicts the "client drops recap_documents" friction logged earlier today by agent:claude-opus-5 on a different query shape — worth noting the field IS present on at least some `--json courtlistener` responses (mine used a two-phrase AND query, theirs used a single quoted phrase on `--type r` alone), so the drop may be query-shape- or code-path-dependent rather than universal. Flagging so maintainers can check whether it's actually two different call sites.

**Friction 1 — `extract` on Justia case-law pages hard-403s (Cloudflare), and there's no fallback path to the same case text. Severity: slowed.** `extract https://law.justia.com/cases/federal/district-courts/<state>/<court>/<case-no>/<id>/56/ --text` returned `AccessBlocker` (cloudflare-managed-challenge, HTTP 403) with no escalation success this pass (the skill doc says browser-passable blocks escalate automatically; this one apparently didn't clear, or the escalation happened invisibly and still failed). I worked around it by using `web` + secondary journalism (a legal newswire, a local weekly) instead of the court's own posted opinion text, which was a fine substitute here but wouldn't be for a case with no press coverage.

**Friction 2 — CourtListener RECAP document *text* is PACER-paywalled even when the document metadata/description is free via search. Severity: slowed, expected but worth naming.** The pivotal document in this task (ECF 172, the trial brief naming <person-i>/<person-c>/<vendor-b>) was fully identifiable by its `recap_documents[].description` field, but `extract`-ing the docket entry URL only returns the same metadata, not the brief's actual argument text — that requires a PACER purchase. Not a tool bug (RECAP genuinely doesn't have the document unless someone already bought and uploaded it), but worth flagging as a standing limit on this task type: a docket search can find *that* a challenge was filed and *who* filed it, but not *what it argued*, without a human with PACER access.

**Worked well — `web` coverage line caught a partial sweep I would otherwise have mistaken for a clean result.** Multiple `web` calls this session reported `RATE-LIMITED: searxng:brave` alongside 20-26 results; I read the coverage line each time rather than trusting the result count alone, per the skill doc's instruction, and it correctly kept me from treating any of these searches as exhaustive.

**Severity:** slowed (two access-blocker detours, both cleanly typed and logged, neither cost more than one extra query round each — the task closed the same session with a tier-1-sourced named story).

## 2026-08-19 · FOIA-routing task, CBP <vendor-a> courtroom-testimony SOW · agent:claude-sonnet-5-parallel-tick2-d
**Task:** determine whether CBP statements of work for four named <vendor-a>/<vendor-a> award IDs are FOIA-gated, and pull the SOW content if not.

**Worked well — `usaspending "<vendor-a>" --limit 30` found all four target awards by recipient name in one call**, and the `--json` output's `meta` block (award_id, amount, dates, description) was exactly what I needed to confirm/correct a task-file citation (one award, `70B03C26P00000169`, was listed unpriced in the task; the client's own data showed $28,800.00 immediately).

**Friction 1 — no way to look up a specific award ID directly. Severity: annoyed, worked around.** `cascade-search usaspending "70B03C26P00000169"` (the award ID itself, not a recipient name) returned a clean typed `VerifiedAbsence` — which is the *correct* typed outcome for "this string isn't a recipient name," but it cost me a call and a moment of doubt before I remembered award IDs aren't names and re-ran with the recipient name + a high `--limit` instead. A `usaspending --award-id <PIID>` mode (or documentation saying explicitly "this searches recipient name, not award ID") would have saved the detour.

**Friction 2 — `extract` on `usaspending.gov/award/{id}` pages returns effectively nothing. Severity: slowed, worked around.** `extract "https://www.usaspending.gov/award/360657558" --ids` reported "729 raw tok -> 4 tok extracted (99.5% reduction)" — that number *looks* like a success (a good reduction ratio) but the 4 tokens extracted were useless; the page is a client-rendered React SPA with no server-rendered payload for `extract`'s DOM/regex approach to find. I worked around it with a direct `curl` to `api.usaspending.gov/api/v2/awards/{id}/`, which is public, unauthenticated, and returned the full structured contract record (solicitation ID, competition type, offers received, funding/awarding office, place of performance) that neither the `usaspending` client nor `extract` surfaced. **Would have helped:** either have the `usaspending` client's award-detail path hit the v2 API directly (it clearly already talks to this API for search), or have `extract` flag "near-zero extraction on a URL with substantial page byte size" as a distinct signal rather than reporting a deceptively good-looking reduction percentage on a near-empty result.

**Friction 3 — no client at all for SAM.gov solicitation/opportunity lookups. Severity: slowed, worked around, but this was the actual finding.** The USAspending record for the on-point award named a `solicitation_identifier` (`70B03C26Q00000152`). That solicitation turned out to be a **public SAM.gov notice** whose full narrative description named the CBP station, unit, and course scope I was tasked with finding — i.e., SAM.gov solicitation text is sometimes a full substitute for a FOIA request, and there's no `cascade-search` path to it at all. I found and read it entirely via direct `curl` to `sam.gov/api/prod/sgs/v1/search/` (keyword/ID search) and `sam.gov/api/prod/opps/v2/opportunities/{id}` (full detail with the notice body) — both unauthenticated, both returned clean JSON, no captcha/Cloudflare encountered. This is the second time in this research's recent history this exact unauthenticated-SAM.gov-API pattern has been the load-bearing access path (an earlier GSA-eLibrary/SAM.gov entity-snapshot note was the first). **This reads like a real gap, not a one-off**: a `cascade-search samgov` client (search by keyword/solicitation number, fetch opportunity detail/description) would directly answer "is this contract action public or FOIA-only" — exactly the question this task existed to resolve — in one typed call instead of three manually-reconstructed API calls.

**Friction 4 — the SOW attachment itself (the actual PDF) is behind a SAM.gov login even though the notice metadata is public. Severity: annoyed, correctly identified as a real boundary not a tool failure.** I could read the full solicitation narrative (which happened to contain most of what I needed) but not the numbered attachment ("Attachment 1 - Statement of Work") — SAM.gov's attachment-download endpoints 404'd on every unauthenticated path I tried. This is a genuine SAM.gov design boundary (free account required, not FOIA-gated) rather than a cascade-search gap, but worth noting for scoping any future `samgov` client: attachment download would need either an authenticated session or should be explicitly out of scope.

**Severity:** slowed overall (no wrong output; the substantive finding — one of four target SOWs is publicly readable without FOIA — came out of manual API reconstruction rather than a tool call, and would have been one clean call with a SAM.gov client).

## 2026-08-19 · re-pull FPDS/USAspending contract table for <vendor-a>, RECOVER-flagged fields · agent:claude-sonnet-5-parallel-tick2-a
**Task:** re-pull a garbled 52-award contract table (<vendor-a>/<vendor-a>) from live USAspending, recovering exact totals, by-sub-agency/by-year breakdowns, and full FPDS competition detail for 8 specific flagged awards, plus check for subcontracts/grants/name-variant recipients.

**Worked well — `usaspending "<vendor-a>" --limit 60` and `--count` matched exactly and fast.** One `--count` call confirmed 52 awards; one `--limit 60` (`--json`) call returned all 52 with clean `meta` fields (award_id, agency, sub_agency, amount, dates, description) — enough to reconstruct the entire by-sub-agency and by-year table without a second tool. This was the core of the task and it worked on the first try.

**Worked well — `VerifiedAbsence` on a recipient-name check gave me a real negative I could put in the research notes.** `usaspending "<vendor-a-dba> LLC" --count` returned `VerifiedAbsence` in exit code 1, which let me write "this DBA has never itself been an award recipient" as a citable finding rather than "I didn't see it."

**Friction 1 — `--all-types` is broken for anything but `--count`. Severity: slowed, worked around.** `usaspending "<vendor-a>" --all-types --count` correctly reports `contracts: 52 | direct_payments: 3 | idvs: 2 | loans: 9`. But `usaspending "<vendor-a>" --all-types` (no `--count`, default or `--limit 70`) fails every time with `outcome: AccessBlocker`, `mechanism: http-5xx`, `detail: "HTTP 422: 'award_type_codes' must only contain types from one group."` — i.e. the flag mixes contract/loan/IDV/direct-payment type codes into one upstream request when listing, but must be issuing separate per-group requests when counting (since count works). This means `--all-types` can tell you a non-contract award *exists* but can never show you *which one* — you have to already suspect the gap and go around the tool to see it. I worked around it with a direct multi-group `curl` to `api.usaspending.gov/api/v2/search/spending_by_award/`, segmenting award_type_codes into contracts/loans/direct_payments/idvs myself. **Would have helped:** either fix the listing path to issue the same segmented per-group requests the count path apparently already does, or have the CLI's own help text say "`--all-types` supports `--count` only, not listing" so I don't find out via a raw HTTP 422.

**Friction 2 — the false-positive is dangerous, not just missing. Severity: slowed, this is the actual finding worth flagging.** Once I reconstructed the all-types listing myself, all 14 non-contract "hits" turned out to be *wrong-entity name collisions* — unrelated SBA COVID-era PPP/EIDL borrowers named "<VENDOR-A>" (two cities in other states — different UEI, different state, nothing to do with the training vendor in question), "BUILD <VENDOR-A>," "TEAM <VENDOR-A>," "TECHNICALLY <VENDOR-A>," "B<VENDOR-A>." The skill doc's guidance ("default matches recipient NAME — precise") describes the *contract* search correctly, but the loan/direct-payment/grant recipient-name matching is evidently much fuzzier than that promise — two of the four exact-string "<VENDOR-A>" hits weren't even the same UEI. If I hadn't individually pulled each award's recipient-location record to check, I'd have reported "14 additional non-federal-training awards" as a real finding about this vendor, when the true number is zero. **Would have helped:** a caveat in the tool's docs (parallel to the existing keyword-vs-recipient-name warning) that recipient-name matching on the *assistance* award types (loans/grants/direct payments) is looser than on contracts, and/or surfacing `recipient_uei`/`recipient_hash` in the `meta` block so a worker can eyeball entity-identity without a follow-up fetch per hit.

**Friction 3 — `extract --text` silently truncates at ~4000 characters, mid-JSON, no truncation notice. Severity: slowed, real risk of silent data loss.** `extract "https://api.usaspending.gov/api/v2/awards/{id}/" --text` (the same page type the tick2-d entry above used as its workaround target) returned output that looked complete — a normal "raw ~N tok -> extracted ~M tok" reduction line — but every one of 7 award records I pulled this way cut off at exactly 4001-4002 characters, mid-field, invalidating the JSON. This happened silently: no warning, no "(truncated)" marker, nothing to distinguish it from a genuinely short document. I only caught it because I tried to `json.loads()` the output and got a parse error on all seven files at almost the identical byte offset. Anyone trusting the extracted text directly (e.g. reading it as prose rather than parsing it) would never notice a mid-record cutoff. I worked around it with direct `curl` (same as tick2-d's independent workaround for the plain award page — this is the *raw API endpoint* version of that same detour, and it also has its own tool-side problem). **Would have helped:** either raise `--text`'s cap for machine-readable content types (JSON in particular, where truncation actively corrupts rather than just shortens), or explicitly flag truncation in the output the way the OCR path already flags itself ("source: OCR ... NOT verbatim").

**Corroborating tick2-d's finding from earlier today:** `extract` on `usaspending.gov/award/{id}` (the React SPA page, not the API) also returned near-zero useful content for me (`raw ~729 tok -> extracted ~4 tok`), same as their report. Direct `curl` to `api.usaspending.gov/api/v2/awards/{id}/` was the right workaround both times — worth promoting to a documented pattern (or building into the `usaspending`/`record` client directly) since two independent workers hit the identical wall and found the identical fix within the same day.

**Severity:** slowed overall (no wrong output shipped — every figure in the final write-up was individually verified against a working access path — but three separate detours: all-types HTTP 422, a false-positive that took real cross-checking to defuse, and a silent truncation bug that could have corrupted every one of the 8 flagged-award records if I hadn't tried to parse them as JSON).

## 2026-08-19 · <county-a> ESAC vendor-attribution FOIA (synthesis-heavy task) · agent:claude-opus-4-8-parallel-tick2-e
**Task:** determine who <county-a> actually paid with federal equitable-sharing "Training and Education" money — a question the ESAC/state-police forms structurally cannot answer, so the job was finding *which corpus could*, not running a lookup. I was asked specifically to report on whether the tool supports **synthesis** rather than just lookup.

**On synthesis specifically — the honest answer is that it supported the two ends and not the middle, and the middle is where the finding came from.** The tool was excellent at *bounding* (proving the federal corpus can't answer this) and at *verifying* (checking my claims against FAR and state-statute text). But the actual discovery — that <county-a> publishes a payee-level Claims Paid Report nobody in three prior passes had found — came from a `web` search whose top-10 I read for *structure* rather than for an answer: result #2 was a PDF agenda packet whose snippet mentioned "3,118 payments." Nothing in the tool surfaced that as significant; it ranked 2nd because of keyword overlap, not because it was the corpus-shaped answer. That's fine — but it means the synthesis step was entirely mine, and the tool's contribution was not burying the lead. Worth knowing that's the current ceiling.

**Worked well — `usaspending --count` + `--limit` turned "we couldn't find it" into a structural argument.** 52 awards / $3,298,091.75, zero mentioning <state-a> or any county. The value wasn't the absence; it was that a *countable* corpus let me say **why** the absence is meaningless — a county spending federal equitable-sharing money isn't a federal contracting party, so USAspending couldn't show this payment even if it happened. A fuzzy source could not have supported that sentence. This is the single best thing the tool did for me.

**Worked well — `extract --grep` against FAR Part 5 and <state-a>'s FOIA statute as a fact-checker.** 140,170 → 3,272 tok and 166,469 → 1,423 tok, and in one case it **corrected me**: I had started to write that the ~$24,500 award clustering sat under the simplified acquisition threshold. It doesn't — the SAT was $150,000 in 2010-11; $25,000 is the FAR 5.101(a)(1) *public-synopsis* threshold. Pulling the regulation text cheaply enough that verifying was reflexive rather than a chore is what caught it. Same for the state FOIA statute (5 business days / 50 free pages / $0.15 cap) — all three went into a FOIA draft as verified rather than remembered.

**Friction 1 — `extract` on a large REMOTE PDF silently returns nothing while reporting "100.0% reduction". Severity: slowed; would have been *blocked* if I'd trusted it. This is the one I'd fix.**
Same file, same pattern, two paths:
```
$ cascade-search extract "https://www.<county-a-domain>/Lists/Events/Attachments/5069/AG%20PKT%20-%2019-05%20COB.pdf" --grep "VENDOR"
== Extract == https://www.<county-a-domain>/...
raw ~3,630,887 tok -> extracted ~3 tok (100.0% reduction)
EXIT=0
```
```
$ curl -sL -o packet2019.pdf "https://www.<county-a-domain>/Lists/Events/Attachments/5069/AG%20PKT%20-%2019-05%20COB.pdf"
$ cascade-search extract packet2019.pdf --grep "VENDOR"
== Extract == packet2019.pdf
source:   PDF text layer (1,046,318 chars, exact)
raw ~261,579 tok -> extracted ~527 tok (99.8% reduction)
[6 matching passages]
EXIT=0
```
**Observation, not conclusion:** the local path prints a `source:` line and reads a 1,046,318-char text layer; the remote path prints no `source:` line and reports raw token count ~14x higher (3,630,887 vs 261,579), suggesting it's measuring raw PDF bytes rather than a decoded text layer — i.e. the text-layer decode isn't happening on the fetched-bytes path. (First observed on a different, larger packet: `AG PKT 26-01 Finance.pdf`, 59MB, `raw ~14,030,552 tok -> extracted ~3 tok` on three different greps.)
**Why it's the dangerous kind:** "100.0% reduction" and exit 0 are exactly what a *successful* extract looks like. I only caught it because zero hits for "VENDOR" in a 59MB county claims register was implausible enough to check by hand. A worker with a more plausible query would have written a verified-absence off a file the tool never read. The OCR path already shouts when its output is untrustworthy — this path should too.
**Would have helped:** emit the `source:` line on every path and fail loudly (or warn) when a PDF yields no decodable text layer, instead of reporting a near-100% reduction on an empty result. Corroborates the pattern in the tick2-a entry above (silent truncation) — same failure *class*: near-empty output dressed as a good reduction ratio.

**Friction 2 — `--json` placement is documented ambiguously and the error doesn't help. Severity: annoyed.**
```
$ cascade-search --wait usaspending "<vendor-a>" --limit 52 --json
cascade-search: error: unrecognized arguments: --json
```
It's a global pre-subcommand flag (`cascade-search --json --wait usaspending ...`). SKILL.md shows it once, as `$CS --json web "query" | jq ...`, in a list where every *other* line puts flags after the subcommand — so the one correct example reads like the odd one out rather than the rule. **Would have helped:** one line in the commands block — "`--json` and `--wait` are global; they go before the subcommand." Cheap fix, and I lost a call to it.
`[fixed 00ca941 — global flags are accepted in EITHER order now, so the command you ran works as typed. Better than documenting the rule: an ordering constraint that costs a call to discover is worth removing rather than explaining.]`

**Friction 3 — no `--json` on `extract`, so structured docs have to be re-parsed by hand. Severity: annoyed, worked around.** These claims reports are genuine tables (VENDOR | NATURE OF CLAIM | DEPT | FUND | AMOUNT | DATE). `--tables` exists and is the obvious fit, but on a 400-page packet where table rows are interleaved with narrative I couldn't get it to give me rows I could trust, so I fell back to `pdftotext -layout` + my own parser. **Would have helped:** `extract --tables --json` emitting row arrays, even best-effort with a confidence flag. Not a blocker — but "the corpus is tabular and the tool reads it as prose" is the recurring shape of this beat's documents (ESAC extracts, state-police XLSX, county claims registers).

**A note on `web` that is a compliment and a caveat.** `web "<county-a> <state-a> vendor payments checkbook transparency accounts payable disbursements"` is what cracked this task — the county's own agenda-packet PDFs surfaced at #2 and #4, and the `*UNIQUE*` markers were right that these were single-engine finds. But the top hit for both of my <county-a>-payment queries was the *<state-a> State Comptroller*, which is the wrong corpus by construction (it holds state-government payments; <county-a> is a unit of local government and its disbursements never pass through it). Ranking put the authoritative-looking-but-structurally-irrelevant source first and the actual answer fourth-ish. No fix implied — just: on government-finance questions the top hit is often the biggest agency rather than the right jurisdiction, and reading down mattered here.

**Severity:** slowed overall. Nothing wrong shipped, and one wrong thing (the SAT/synopsis-threshold mixup) was caught *by* the tool. But Friction 1 is a live correctness hazard for exactly the "defensible negative" use case the tool exists for — a silent empty read on a large remote PDF is indistinguishable from a clean extraction.
`[fixed 00ca941 + 182db4a — remote PDFs are decoded rather than read as bytes, and a zero-match grep says so explicitly instead of reporting a triumphant reduction over nothing. Your framing of the hazard is the one the fix was written against: an empty read that looks like a clean extraction is a false absence manufactured inside the tool, which is the single thing it must never do. `--tables --json` remains unfixed and tracked.]`

## 2026-08-19 · <vendor-a> parent-task synthesis pass (no field research) · agent:claude-sonnet-5-parallel-tick3-c
**Task:** claim the <vendor-a>/<vendor-a> PARENT task, read every child task's work log + the entity profile, and update the parent's own deliverable with a cross-cluster synthesis and a highest-value-gap recommendation. Explicitly not a cascade-search research task — I never called `cascade-search` myself this pass, so I have nothing to add to today's rich thread above about `--all-types`, `extract` truncation, or `web` ranking. This entry is about the research-notes CLI (Pyrite) instead, which is in scope per the skill's own framing ("report friction on a tool you just used").

**Friction — the state machine rejects a `claimed → done` transition with no shortcut, even when the work is genuinely already done. Severity: annoyed, cheap workaround.**
```
$ pyrite task update "<task-id>" -k <kb> -s done
ERROR [VALIDATION_FAILED]: Cannot move task from 'claimed' to 'done'. Allowed
next: in_progress, cancelled. Tasks follow open → claimed → in_progress →
done/failed/blocked/review; walk through the intermediate states rather than
skipping (or use 'cancelled' to retire an obsolete task from any state).
```
This was a human-action ticket: assignee had already been hand-set to a human-session marker by whoever filed the follow-up notes, and the actual work (a <state-b> Secretary of State interactive session) was visibly done — both target notes (the <vendor-a> entity profile and the <person-h> profile) already carried the resolved finding. The task file itself was just never advanced past `claimed`. The error message is actually good — it names the exact allowed-next set and the intended path — so I could self-serve immediately (`-s in_progress` then `-s done`, two calls). Not a real blocker. But it's worth noting the specific shape: a *human*-attributed completion (assignee is a human-session marker, not an agent) still has to walk the same agent-oriented state ladder as a machine-claimed task, which reads a little oddly — there's no "record a completed human action" shortcut distinct from "advance a claimed-but-unstarted-by-anyone task."
**Would have helped:** nothing urgent — the error message already told me the fix. Maybe worth a one-line callout in the skill's Step 11 ("Transitions") section noting that `claimed`/`review`/`blocked` states sometimes need an explicit intermediate `-s in_progress` hop even when you're just closing out someone else's already-finished work, since a first-time reader might reasonably try `claimed → done` directly (I did) and only learn the ladder from the error.

**Worked well — `task list -f json` piped through a small python filter was the fastest way to get a clean cross-child status table.** For a synthesis task specifically (as opposed to a single-task research pass), what I actually needed first was "what state is every sibling task in, right now" — a single `task list -k <kb> -f json | python3 -c "..."` grep-and-print got me a full status/priority/assignee table for the ~15 sibling tickets in one call, which is what let me tell (before reading a single work log) which threads were live-claimed by other agents this tick (stay off) vs. genuinely stale/open (worth flagging as the next dispatch). This isn't a novel pattern relative to the skill doc, but it's worth confirming it scales fine to a ~15-sibling family with no friction.

**Observation, not a complaint — the entity profile itself is where most of the real synthesis already lived.** By the time I read the <vendor-a> entity profile in full, nearly everything I would have wanted to say in a parent-task synthesis was already stated there, per-section, by the workers who wrote it (each section timestamped and self-correcting against the master memo). The parent task's own "Work Log" ended up being a pointer-and-triage document — what's closed, what's live, what's genuinely untouched — rather than new analysis, because the analysis had already happened in the entity profile. Not a tool problem, just worth naming for whoever designs the next parent-task synthesis prompt: if the entity profile is well-maintained, the parent task's marginal value is triage (which sibling to dispatch next), not restating findings.

## 2026-08-19 · draft-revision task — verifying Du Bois 1900 Paris Exposition facts · agent:claude-sonnet-5-parallel-tick3-d

**[tracked]** Browser allow-list gap, the second most-reported friction. Root cause addressed 2026-09-19: the list was hardcoded with no override, so every registry needed a commit. It is now extendable via `~/.evidence-search/allowed_hosts.json` with a required reason; run `evidence-search hosts`. The specific host in this entry still needs adding, but you can now add it yourself.

**Task:** a draft-revision ticket, so most of the work was reading corpus, not searching. The one place I genuinely needed the open web was verifying new claims about Du Bois's 1900 Paris Exposition data portraits — this material did not exist anywhere in the research notes and I was about to make it the piece's cold-open artifact, so it needed independent verification before I'd trust it in the draft.

**Worked well — `web` gave a clean, high-precision first pass.** `cascade-search web "Du Bois 1900 Paris Exposition data visualization charts Library of Congress" --wait` returned the LOC's own resource guide and item record at #1-3, Public Domain Review and the "Exhibit of American Negroes" Wikipedia page right behind — i.e. tier-1 (LOC) and reasonable tier-2 sources both surfaced on the first query, no query reformulation needed. Coverage line (`79/82 responsive`) made it easy to trust the completeness of that pass without extra work.

**Worked well — `extract --text` on the LOC resource guide and Public Domain Review pages was exactly the right tool.** Two calls, both fast, both returned readable prose with the facts I needed (Calloway/Murray collaborators, chart counts, Grand Prize) without me having to wade through page chrome. No friction.

**Friction — `extract` on `loc.gov/pictures/item/...` hit a Cloudflare `AccessBlocker`, and I didn't retry through the browser-escalation path. Severity: cosmetic/non-blocking.**
```
$ cascade-search extract "https://www.loc.gov/pictures/item/2005679642/" --text --wait
== AccessBlocker ==
mechanism: cloudflare-managed-challenge
detail:    HTTP 403
ESCALATABLE: a real browser session could plausibly pass this gate.
```
The typed outcome did its job — I knew immediately this was a tooling block, not evidence the page/collection doesn't exist, and moved on to corroborating the same facts (chart count, item description) via other tier-1/tier-2 sources rather than treating the gap as a finding. I didn't invoke the browser escalation myself since loc.gov is exactly the kind of public-records source the allow-list should cover and a secondary LOC page (the resource guide) had already given me what I needed — but flagging in case that page specifically is a recurring block worth pre-escalating for future workers who need the item record directly rather than just corroboration.

**Net:** clean, fast, no wasted calls. Two `web`/`extract` calls got me from zero corpus coverage to three independently-corroborated facts (collaborators, chart count, reception/Grand Prize) I was comfortable citing in the draft. This is a small, low-drama use case relative to today's other entries, but it's the kind of "does this actually save time on a real verification need" test the tool should keep passing.

## 2026-08-19 · award-withdrawal task (<vendor-i>, $150M ORR) · agent:claude-opus-4-8-parallel-tick3-a

**[tracked]** Web/SearXNG rate-limiting cluster, the most-reported friction in this log. Partly root-caused 2026-08-30: the SearXNG daemon was running against a deleted settings path, and four default general engines are durably blocked. Re-reports add corroboration, not new signal.

**Task:** find why a $150M ORR single-source cooperative agreement to a <city-a> law firm was withdrawn 11 days after it was announced, what replaced it, and who the firm is. `fedreg` + `usaspending` + `courtlistener` + `extract`, plus WebSearch.

**Worked well 1 — `fedreg` answered the central question outright, and its two-result precision was itself the finding.** `cascade-search --wait fedreg "<vendor-i>"` returned exactly the two notices as results #1 and #2 (both `*UNIQUE*`), and — this is the part that mattered — nothing else <vendor-i>-related in the whole corpus. "There is no re-announcement of the $150M" is a claim I could only make because the corpus is defined and the result set was clean. Results #3-20 were obvious noise (Medicare OPPS, gas pipelines, marine mammals) matching on the firm name's three words separately, which is fine and self-evidently ignorable. This is the tool doing exactly what it's for.

**Worked well 2 — `extract --text` on a paywall-adjacent news page, 95.5% reduction, and it beat WebFetch outright.** WebFetch on a regional newspaper's site returned "unable to fetch" and on a state news site returned a hard `HTTP 403 Forbidden`. `$CS --wait extract "https://www.<outlet-d-domain>/.../<id>/..." --text` pulled the full AP wire story at ~31,788 → ~1,419 tokens, and that single extraction carried the ORR press statement, the firm's "small portion" quote, the <person-j>/<person-k> roster detail, the <org-b> $20M/through-December replacement figure, AND the <org-c> plaintiff quote. Four of my five open questions came out of one call. `extract` on a syndicated-wire mirror is a better move than WebFetch on the paywalled original, and that's now twice in this log.

**Worked well 3 — `usaspending --count --all-types` gave me the publishable negative the writeup needed.** `VerifiedAbsence` on "<vendor-i>" is load-bearing in the artifact ("no federal money of any type ever reached this firm"), and I would not have written it that confidently off an empty list.

**Friction 1 — I re-committed the documented `head` exit-code mistake, on the same day it's warned about two entries above mine in this file. Severity: annoyed, self-inflicted, but suggests a fix.**
```
$ $CS --wait usaspending "<vendor-i>" --count --all-types 2>&1 | head -25
echo "=== EXIT: $? ==="     # printed 0
```
Then, unpiped:
```
$ OUT=$($CS --wait usaspending "<vendor-i>" --count --all-types 2>&1); echo "TRUE EXIT: $?"
TRUE EXIT: 1
```
**Observation:** exit was 0 through the pipe, 1 without. **Conclusion:** the tool is correct and I was wrong — same as the two agents cited in SKILL.md. What's interesting is that I had *read that exact warning* in SKILL.md maybe fifteen minutes earlier and still did it, because piping to `head` is muscle memory for "don't blow up my context," and the outcome word `VerifiedAbsence` was printed right there in the body so I wasn't reading for the exit code at all. **Would have helped:** the human-readable output already prints the outcome name — that's what saved me. So the fix may be docs-side and small: SKILL.md teaches `if $CS ...; then` exit-code branching in its very first example, *then* warns about pipelines 150 lines later under "Report the friction." Putting the pipeline caveat immediately adjacent to the exit-code branching example (where the reader forms the habit) would land better than putting it in the feedback section (where the reader is already done). Cheap doc reorder; this is now the third independent report of the same stumble.

**Friction 2 — `web` returned zero results with 78/82 coverage and honestly refused to certify, which is correct behavior, but `retry after: Nones` is a broken string. Severity: cosmetic (the `Nones`), annoyed (the empty result).**
```
$ $CS --wait web "\"<vendor-i>\" <city-a> immigration ORR \$150 million unaccompanied children" 2>&1 | head -40
== RateLimited ==
coverage: 78/82 responsive | 1 distinct index(es) | RATE-LIMITED: searxng:brave, searxng:google cse | ERRORED: searxng:duckduckgo, searxng:startpage
retry after: Nones
detail:    Cannot certify absence: coverage incomplete ...
```
`retry after: Nones` is a `None` interpolated into an f-string with an `s` suffix — should suppress the line entirely when there's no retry-after value, or print `unknown`. Tiny, but it's the kind of thing that makes an agent wonder if it mis-parsed the output.
The substantive half: this was a *heavily* loaded query (quoted phrase + five extra terms) and the story was on the front page of national and state outlets that week, so zero results with 78/82 engines responding is a surprising miss. I switched straight to WebSearch, which returned ten on-point outlets on the first identical-intent query. **Not filed as a bug** — SKILL.md is explicit that `web` is a discovery tier whose engines don't honour quoted phrases, and that a `web` zero is a weak negative; the tool told me so and downgraded correctly. But the practical lesson for the docs is narrower than "web is fuzzy": **`web` degrades badly on long queries with a quoted phrase, precisely the query shape an agent writes when it wants a specific entity.** One line — "keep `web` queries short; move entity precision to a corpus source" — would have saved me the call.

**Where the results fell short (corpus gap, not a bug) — `courtlistener '"<vendor-i>"' --type r` was the wrong corpus for the question, and its `*UNIQUE*` hits are false friends.** 20 results, `total_matches` present, all fine mechanically. But the firm was founded in 2023 and had no federal award, so the phrase matches mostly as *counsel of record on unrelated dockets* (an unrelated towing case, a state-v-EPA case, an opiate MDL) — a fuzzy-name-collision problem that the recipient-name-vs-keyword caveat in SKILL.md covers for `usaspending` but nothing covers for `courtlistener`. Result #5 *was* <case-a>, which looked like a hit and was really the phrase appearing in a docket I'd already read from another route. **Would have helped:** a caveat parallel to the usaspending one — "a quoted firm name in `courtlistener` matches attorney-of-record appearances, not party status; use `--type d` or read the party field before treating a hit as involvement." I nearly recorded "<vendor-i> appears in the <case-a> docket" as a finding before checking what the match actually was.

**Note on the task rather than the tool, since it affects dispatch:** the ticket said "the corpus has almost nothing on it," but a prior pass had already archived both FR notices in-repo with SHA-256 and written a source note containing the withdrawal's operative sentence. My first `fedreg` call was therefore redundant with work already on disk. A research-notes search before the first cascade-search call is what caught it — worth keeping that ordering in the worker skill (it already is, Step 5 item 1; I'm confirming it earns its place).

**Severity:** slowed at worst. All four research questions were answered, three from tier-1 primary documents, and the one confident-wrong-answer risk in the task (asserting the injunction caused the withdrawal) was avoidable because the *primary document itself* named the actual cause. `fedreg` + `extract` did the real work here.

## 2026-08-19 · <county-a> Claims Paid multi-month sample · agent:claude-sonnet-5-parallel-tick3-b

**[fixed — see the 2026-08-19 FOIA follow-through task (vendor attribution) entry below, re-verified against the same file/command shape the same day]**
**Task:** sample 18 <county-a> Board agenda-packet PDFs (Finance and Budget Committee, 2016-2023) for
Sheriff training-category payee rows, checking for a handful of specific vendor names ("<vendor-a>" etc).
The task prompt explicitly warned that `extract` had previously grepped raw PDF binary on these large
county packets and reported a false "100.0% reduction, zero matches" absence, and said this was "fixed
within the last hour" — verify the `source: PDF text layer (N chars, exact)` line is present before
trusting a result. So this session both used the tool and specifically re-tested the claimed fix.

**The remote-PDF text-layer fix is real and verified — worked well.** On the actual packet PDF I was
working with (`AG PKT 23-10 Finance.pdf`, 1.57M chars, ~394K raw tokens):
```
$ cascade-search extract "https://www.<county-a-domain>/Lists/Events/Attachments/6723/AG%20PKT%2023-10%20Finance.pdf" --grep "Sheriff" --wait
== Extract == https://www.<county-a-domain>/Lists/Events/Attachments/6723/AG%20PKT%2023-10%20Finance.pdf
source:   PDF text layer (1,578,283 chars, exact)
raw ~394,570 tok -> extracted ~3,943 tok (99.0% reduction)
  [Sheriff] ...Tree Services T. Resolution: Authorizing a Contract Extension...
```
Real, legible, correctly-attributed matches — the `source:` line is present and accurate, char count
matches the actual document. This is a genuine fix relative to what the task described, confirmed on a
live remote URL, not just a local file.

**Friction — but `extract --grep` still returns a false absence on this exact same document, for terms
later in the file, with no error and no signal that anything went wrong. Severity: blocked (would have
cost the session's one real finding had I trusted it and not fallen back to pdftotext).**

I never actually ran `extract --grep "<vendor-a>"` during the main research pass — the task told me to
use `pdftotext -layout` locally instead and I did, which is how I found "<vendor-a> interdiction
workshop hotel" ($531.48 + $905.80, Sheriff p-card section) buried around line 1166 of a 22,430-line
extracted text file. After finding it by hand, I went back and specifically re-tested `extract --grep`
against the *exact same remote URL* to see whether the claimed fix would have caught it:
```
$ cascade-search extract "https://www.<county-a-domain>/Lists/Events/Attachments/6723/AG%20PKT%2023-10%20Finance.pdf" --grep "<vendor-a-word>" --wait
== Extract == https://www.<county-a-domain>/Lists/Events/Attachments/6723/AG%20PKT%2023-10%20Finance.pdf
source:   PDF text layer (1,578,283 chars, exact)
raw ~394,570 tok -> extracted ~28 tok (100.0% reduction)
$ echo $?
0
```
No match lines, no "no matches found" message, exit 0 — the *exact same output shape* the task warned
about, except this time the `source:` line is genuinely accurate (the fix for the binary-grep bug is
real), so the failure mode has moved rather than disappeared. I confirmed "<vendor-a-word>" (capitalised) appears
twice in the document via `grep -c -i <vendor-a-word>` on my own `pdftotext -layout` output of the identical PDF —
ground truth is a real hit, not a typo on my part. To rule out case-sensitivity or a stopword issue I
retried with `"<vendor-a>"`, `"interdiction"`, `"<VENDOR-J>"`, and `"<vendor-k>"` — all four are
verified-present strings in the same p-card table (<vendor-j> LLC and <vendor-k> Inc. are payee names a
few hundred lines from "<vendor-a>" in my local extraction) — **all four returned the identical
`~28 tok / 100.0% reduction / zero match lines / exit 0` shape.** Only `--grep "Sheriff"` (which appears
in the document's very first pages, in resolution titles) returned real matches.

**Working theory, not confirmed:** this looks like a scan-depth/truncation issue rather than a decode
issue — `extract` appears to search only a prefix of the 1.57M-char text layer (department-agenda-item
titles near the front all matched; specific payee-table rows ~1,100+ lines into the extracted text did
not), while still reporting the full document's char count in the `source:` line. That combination — full
char count reported, but only a prefix actually searched — is what makes the absence look trustworthy
when it isn't. I did not instrument the tool internals to confirm this, so treat it as a hypothesis a
maintainer with source access should check, not a diagnosis.

**Would have helped:** either (a) print something explicit when zero grep matches are found —
`0 matches in N chars scanned` — distinguishing "scanned everything, found nothing" from silence, or
(b) if there is a scan-depth cap on large PDFs, name it in the output the same way the coverage line names
engine gaps in `web` (`80/82 responsive`) — e.g. `scanned: 340,000/1,578,283 chars`. Either would have let
me trust or distrust the result without falling back to a second tool.

**Net for this task:** no harm done, because the task prompt itself told me not to trust `extract` here
and to use `pdftotext -layout` locally, and that is exactly what surfaced the finding. But the specific
claim "this is now fixed, if you don't see the source line something is wrong" is only half true on this
document: the source line is present and correct, and the result is *still* wrong. A worker without the
task's explicit warning, seeing an accurate-looking `source:` line and a clean `100.0% reduction`, would
have reported "<vendor-a> does not appear in the September 2023 <county-a> packet" — which is false.

**Worked well, unprompted by the task — discovering the county's own SharePoint list API.** Not a
cascade-search finding, but worth naming since it's a reusable pattern other county/municipal-government
tasks in this research will hit: `<county-a-domain>` runs on SharePoint, and its committee page
(`/Pages/CountyBoard/committee.aspx?cID=7`) embeds the exact `_api/web/lists/getbytitle('Events')/items?
$select=...&$expand=AttachmentFiles&$filter=Status eq 'Scheduled' and Committee eq <id>` query it uses
client-side to render the meeting list. Calling that endpoint directly with `curl` + `ACCEPT:
application/json;odata=nometadata` returned all 334 Finance Committee meetings back to 1999 with their
packet-attachment filenames and URLs in one shot — far better than guessing packet URLs via `web`/
WebSearch one month at a time, and something `cascade-search` has no dedicated client for (reasonably —
this is one CMS's private-but-undocumented API, not a general public-records source). Flagging in case a
"government SharePoint sites often expose their event/document list via `_api/web/lists`" pattern note
belongs somewhere findable for future county-government tasks, since it isn't specific to <county-a>.

## 2026-08-19 · FOIA follow-through task (vendor attribution, <vendor-a> training dollars) · claude-sonnet-5

**Context:** re-checked the same large <county-a> PDF (a local copy of the October 2023 board agenda packet,
1,578,283 chars) that an earlier pass on this research flagged for the now-supposedly-fixed
`extract --grep` global-match-cap bug (see prior entry above, same file). The task prompt told me the fix
had landed within the hour and asked me to re-verify if I hit that behavior again.

**Command:**
```
$CS extract "$F" --grep "<bank-a>" --grep "Procurement Card" --grep "Purchase Card"
```
**Got:** `'<bank-a>' matched 223 times, showing first 40; 'Procurement Card' matched 98 times, showing
first 40` — each pattern reports its own true count and its own cap notice. Ran `--grep "Purchase Card"`
alone for comparison: 2 real matches, no "showing first N" line at all (nothing to truncate). This is
exactly the per-pattern behavior the fix was supposed to produce, and it held under a real multi-pattern
call against the same large document that broke the old behavior.

**Verdict: fix confirmed working, on the exact file and command shape that exposed the original bug.**
No silent starvation observed this session across several multi-`--grep` calls against this and two other
large county PDFs (1.19M and 1.52M chars).

**Separately, a genuine result-quality finding (not a cascade-search bug, a document-structure trap):**
the two dollar amounts I needed to trace ($531.48, $905.80) live in a "Procurement Card Activity Report"
table that has NO Fund column — merchant, department, amount, date, business-purpose text, nothing else.
The payee-level "Claims Paid Report" in the *same PDF* does carry a Fund column but aggregates procurement-
card charges under the card issuer ("<bank-a> Bank") with no per-transaction line, and there is no
shared invoice/transaction ID between the two tables. `extract --grep` retrieved both tables correctly and
completely (verified this session) — the gap is structural to the source document, not a tool limitation.
Noting it here only because it's the kind of "the tool worked, the source still can't answer the question"
case the skill doc asks for; no action item for cascade-search itself.

**Worked well:** `--grep` with multiple patterns in one call, run against a 1.5M-char PDF, returned in
seconds with correct per-pattern counts and exact quoted context windows — this is what let me rule out
(not just fail to find) a fund-path match for two specific dollar amounts across the full document, twice,
with different pattern sets, and trust a negative result without re-reading the PDF by hand.

**Severity:** n/a — this is a fix-verification entry, not a new complaint. Recommend marking the prior
entry `[fixed]` with a pointer to this one.

## 2026-08-19 · brief-writing task (dating a statutory mandate) · claude-sonnet-5

**[tracked]** Web/SearXNG rate-limiting cluster, the most-reported friction in this log. Partly root-caused 2026-08-30: the SearXNG daemon was running against a deleted settings path, and four default general engines are durably blocked. Re-reports add corroboration, not new signal.

**Command:** `$CS fedreg "detention bed mandate" --from 2003-01-01 --to 2005-12-31 --wait`
**Expected:** Either a Hit pointing at something plausible, or a clear signal that the Federal Register isn't where this document type lives.
**Got:** `Hit`, exit 0, 11 results — all completely unrelated (endangered-species critical-habitat rules, hours-of-service trucking rules, ADA accessibility guidelines). None mentioned detention or immigration. The tool returned a confident-looking `Hit` for a query that had zero real matches in the corpus it searched.

**Friction:** The task called for dating a "congressional detention bed mandate." That mandate is an *appropriations-bill line item* (a rider in a DHS Appropriations Act), not a Federal Register rule/notice — Federal Register covers agency rulemaking, not appropriations riders passed by Congress. `fedreg` doesn't know this and doesn't say so; it just relevance-ranks whatever's in its index against the query terms and returns the top N with a `Hit` verdict, even when every result is noise. Per the skill doc's own warning ("relevance-ranked though, so a row count is not a measurement") I didn't take the row count as a finding — but a first-time user without that warning fresh in mind could easily skim 11 titles, see nothing obviously wrong, and half-trust it.

**Worked around it:** switched to `$CS web "..."` (general web/SearXNG tier) which correctly surfaced congress.gov, govinfo.gov, and multiple advocacy-org citations pointing to the actual two statutes involved (P.L. 108-458 IRTPA §5204, and P.L. 111-83 DHS Approps Act 2010) — then went directly to govinfo.gov via `$CS extract` and pulled the codified statutory text to confirm both dates and language. That combination (web search to find the right primary-source URL, then extract to pull the exact clause) is what actually answered the question; `fedreg` contributed nothing to the finding despite returning a `Hit`.

**Would have helped:** `fedreg` distinguishing "no relevant hit above some relevance floor" from "here are the top N regardless of relevance" — even a low-confidence flag on the result set (e.g., "top result score below typical-hit threshold") would have saved the detour of reading through 11 titles to confirm none were on-topic. Not asking for corpus expansion — appropriations riders genuinely aren't Federal Register documents, so this is a "wrong tool for this document type" case, not a coverage gap. A one-line hint in the tool's own output ("no Federal Register document is expected to contain congressional appropriations language — try `web` or a congress.gov lookup for statutes/riders") would turn a silent near-miss into a fast redirect.

**Worked well:** `$CS extract <govinfo.gov URL> --grep "5204" --grep "detention" --grep "8,000"` on the full codified IRTPA text (raw ~205,789 tok) returned exactly the clause needed at ~1,432 tok — a 99.3% reduction — and it was the *exact* statutory language ("shall increase by not less than 8,000... subject to the availability of appropriated funds") that let me catch and correct an imprecise date claim in the task ticket itself (ticket said "2004 congressional bed mandate"; the real enforceable floor is P.L. 111-83, enacted 2009-10-28). `web`'s coverage-honesty line (`80/82 responsive | RATE-LIMITED: brave | ERRORED: startpage`) also did exactly what the skill doc promises — I could see it wasn't silently missing engines.

**Severity:** annoyed (fedreg) / worked-well (web + extract combo)

## 2026-08-19 · historical fact-check task (Du Bois 1900 Paris Exposition) · claude-sonnet-5

**[tracked]** Browser allow-list gap, the second most-reported friction. Root cause addressed 2026-09-19: the list was hardcoded with no override, so every registry needed a commit. It is now extendable via `~/.evidence-search/allowed_hosts.json` with a required reason; run `evidence-search hosts`. The specific host in this entry still needs adding, but you can now add it yourself.

**Command:** `$CS extract "https://www.loc.gov/pictures/item/2005679642/" --text --wait`
**Expected:** Either a `Hit` with the LOC item page content, or an `AccessBlocker` naming the mechanism so I know to route around it (per skill doc, this host was flagged in advance as a likely Cloudflare block).
**Got:**
```
== AccessBlocker ==
query:    https://www.loc.gov/pictures/item/2005679642/
coverage: 0/0 responsive
mechanism: cloudflare-managed-challenge
url:       https://www.loc.gov/pictures/item/2005679642/
detail:    HTTP 403
NOT a negative finding. Access was blocked.
ESCALATABLE: a real browser session could plausibly pass this gate.
```
Correction to my own draft of this entry: I initially misread the exit code as 0 because I'd captured `$?`
after a compound command (an `echo` following the real call in the same line), not from the `cascade-search`
process itself — classic case of the exact "reconstructed, not copied" trap the skill doc warns about. Ran
it again cleanly, redirected straight to a file with nothing chained after it: **actual exit code is 2**,
correctly matching `AccessBlocker` per the skill doc's table. No bug here — flagging only because I nearly
filed a false report and the near-miss itself seems worth a line: the block-text output plus a piped/chained
caller makes it easy to misattribute `$?`, same failure mode the skill doc already documents from a prior
agent's `head` mistake. Not a tool fix, a "the docs' warning is earning its keep" data point.

**Friction 1 — "ESCALATABLE" is misleading when the host isn't on the allow-list.** The block message says
"a real browser session could plausibly pass this gate" — true of the Cloudflare challenge itself, but
`loc.gov` is not in `cascade_search/core/browser.py`'s `ALLOWED_HOSTS`, so escalation was never going to be
attempted regardless of whether a browser could pass it. I had to go read the source (`browser.py`) to
learn this — the `extract` failure message doesn't distinguish "blocked, and escalation will be tried" from
"blocked, and escalation isn't even allowed for this host." The task I was on explicitly asked me to check
this distinction ("check whether loc.gov qualifies; if not, the blocker is real"), so I went looking anyway
— but a caller who didn't know to ask would read "ESCALATABLE" and reasonably conclude the tool would try,
when for this host it structurally cannot.

**Would have helped:** have the blocker message itself say "not on browser-escalation allow-list" when
that's the actual reason escalation won't happen automatically, rather than the generic "ESCALATABLE"
framing that's accurate about the *mechanism* (a browser could pass this Cloudflare challenge) but silent on
whether *this run* will ever attempt it (it won't, for a non-allow-listed host).

**Worked well — the JSON-API workaround.** `loc.gov` publishes a `?fo=json` catalog variant of every item
page (e.g. `https://www.loc.gov/item/2005679642/?fo=json`) that is **not** behind the same Cloudflare gate
as the HTML page. `$CS extract "https://www.loc.gov/item/2005679642/?fo=json" --grep "contents|description"`
returned the full primary catalog record cleanly — exact item description, contents breakdown by item
number range, contributor names — everything needed to correct a factual error (a secondary source claimed
32 items in "The Georgia Negro"; the LOC's own catalog record says 36). This wasn't something cascade-search
did automatically; I found it by knowing LOC's API convention independently. Worth considering whether
`extract`/the browser layer could try a `?fo=json` variant automatically on `loc.gov/item/` URLs before
giving up — it would turn a real, structural block into a non-issue for this whole class of source, which
recurs across the corpus (LOC digitized collections are a common primary source for historical claims).

**Severity:** Friction 1 — annoyed (had to read source to get the real answer; message itself is accurate but
incomplete for this case). JSON-API finding — worked well, but only because I already knew to try it;
flagging as a possible tool improvement, not filing as a bug. Exit-code note above — no severity, self-corrected
before filing, logged only as a documentation-warning success story.

---

## 2026-08-19 · vendor-market mapping task (<vendor-a> cluster) · claude-opus-4-8-parallel-tick4-b

Vendor-market census: was <vendor-a>/<vendor-a> an outlier or an instance? Ran ~25 `usaspending`
count/group queries, ~6 `courtlistener`, ~5 `web`, 2 `extract`. **The tool answered the ticket's
structural question, and five prior passes using consumer web search had failed on it.** Detail below,
but the headline for maintainers: `usaspending` recipient-name search is the thing that broke a
question that had been stuck in this research for eight days.

**Friction 1 — `courtlistener` has no `--format json`, and the failure is a Python traceback.**

**Command (exact):**
```
$CS courtlistener '"<vendor-l-brand-1>"' --type r --wait --format json 2>/dev/null | python3 -c "...json.load(sys.stdin)..."
```
**Expected:** JSON, because my dispatch prompt told me to "read `meta.total_matches`" — which implies a
structured output mode, and `usaspending` habits primed me for one.
**Got:** my parser blew up on empty stdin for all four queries. `courtlistener --help` shows the real
flag set (`--type/--court/--cursor`) — no `--format`. Because I had `2>/dev/null`, argparse's error was
swallowed and all I saw was my own traceback; I initially misread it as an output-shape problem rather
than an unknown-flag problem.
**Had to figure out:** that `meta.total_matches` and `meta.next_cursor` — both named in the help text and
in my dispatch prompt — are **not reachable from the CLI at all**. `--cursor` says "pass
meta.next_cursor from a prior result," but no CLI output mode prints `meta.next_cursor`. That is a
genuine dead end in the interface: the help text documents a paging workflow whose input the tool never
emits.
**Would have helped:** either a `--format json` on `courtlistener` (matching the affordance the help text
already assumes), or — cheaper — print `total_matches` and `next_cursor` in the human-readable footer.
Right now `results: 20` is ambiguous between "20 matches" and "20 shown, N total," and the standing
caveat in my dispatch ("read meta.total_matches, a row count is not a count") is un-actionable as built.
**Severity:** slowed. It also degraded a finding: I could not distinguish whether `"<vendor-l-brand-1>"`'s 20
rows were the whole match set or a page, so I wrote it up as unverified rather than as reach evidence.
`[fixed ec88f4c — the header now prints `results: N of M total_matches`, so a 20-row page can no longer be mistaken for a complete match set. Your note that the dispatch caveat was "un-actionable as built" is the sharpest statement of the problem in this file: the doc told workers to read a field they could not reach. Three independent reports; one line of output.]`

**Friction 2 — `extract --grep` returning "~3 tok / 100.0% reduction" reads as success but means zero matches.**

**Command (exact):**
```
$CS extract "https://www.courtlistener.com/docket/<docket-id>/<case-slug>/" --grep "<vendor-l-brand-1>" --grep "<person-l>" --grep "<vendor-l-brand-2>"
```
**Got:** `raw ~184,708 tok -> extracted ~3 tok (100.0% reduction)` and nothing else. Same shape on a
second call against a USAspending award page (`raw ~729 tok -> extracted ~3 tok (99.6% reduction)`).
**Friction:** "100.0% reduction" is the tool's own success metric maxing out, so at a glance it reads as a
triumphant compression, when what actually happened is **no pattern matched and there is no content**.
This is the same hazard class as the `--ids` entry already in this log: a confident-looking presentation
of an empty answer. It cost me a real inference — I could not tell whether the CourtListener row was an
index-level match (docket metadata) or a document-body match, which is exactly the attorney-of-record-vs-
substantive-reference distinction my dispatch prompt warned about.
**Would have helped:** when the extraction is empty, say so in words — `NO MATCHES for --grep patterns
(3 patterns tried)` — instead of reporting a reduction percentage. A zero result and a 100% reduction are
the same number but not the same finding.
**Severity:** slowed, and near-miss on a wrong conclusion.
`[fixed eef4697 — keyword-vs-recipient-name collisions now warn and name the distinct recipients matched.]`

**Friction 3 — `usaspending --group contracts --limit 30` caps output below the vendor's award count, with no total.**

**Command (exact):**
```
$CS usaspending "<vendor-m>" --group contracts --limit 30 --wait
```
**Got:** 30 rows for a vendor that `--count` reports as **45 awards**. No aggregate obligation figure
anywhere in either mode — `--count` gives award *counts* only, `--group contracts` gives per-award
dollars. To get "what has this vendor been paid," I had to scrape the printed rows with a regex and sum
them in Python, once per vendor, ~10 times.
**Friction:** every dollar total in my deliverable is therefore a **floor**, not a total, for any vendor
with >30 awards (<vendor-m>, <vendor-n>, <vendor-o> 253, <vendor-p> 220). I had to caveat the census table
accordingly, which materially weakens it — "at least $217,454" is a much weaker claim than "$217,454".
**Would have helped:** a `--sum` / `--total` on `usaspending` that returns total obligated across ALL
matching awards, not just the displayed page. For a procurement-heavy beat this is the single most
common question asked of the tool and it is currently the one thing it will not answer directly.
**Severity:** slowed (≈10 regex-and-sum detours), and it degraded the output's evidentiary strength.
`[fixed 5f46710 — `usaspending --sum` returns a real total and says whether it is complete or a floor, which is what removes the hand-summing detours. 08fa7ae made it honour --from/--to rather than silently ignoring the bounds and still calling itself complete.]`

**Worked well — and specifically, this is what cracked the ticket.**

`usaspending` recipient-name search with the `VerifiedAbsence` vs `Hit` distinction is what made this pass
succeed where five prior passes failed. Concretely:

```
$CS usaspending "<vendor-l-brand-1>" --count --wait -> VerifiedAbsence
$CS usaspending "<vendor-l-brand-2>" --count --wait -> VerifiedAbsence
$CS usaspending "<vendor-l>" --count --wait         -> Hit, 27 awards
```

Same company, three names. Prior passes searched the **brands** and correctly got nothing; the money is
filed under the **holding company**. What made this legible rather than confusing was the typed outcome —
because `VerifiedAbsence` is asserted as a *publishable negative* rather than a shrug, the contrast with
`Hit` on the third name was immediately interpretable as a naming problem rather than as noise. A plain
"0 results" three times would have looked like the same dead end the prior passes reported. **The typing
is doing real epistemic work, not just ergonomics.**

Corollary worth flagging for the docs: a `VerifiedAbsence` on a *brand* is not a `VerifiedAbsence` on a
*firm*. That is not a tool bug — the tool answered exactly what was asked — but it is a footgun specific
to `usaspending`'s recipient-name matching, and it would be worth one line in the help text: "matches the
registered recipient name; resolve the corporate parent before treating an absence as dispositive."

Also worked well: `web` reporting coverage honestly (`80/82 responsive | RATE-LIMITED: searxng:brave |
ERRORED: searxng:startpage`) and **refusing to certify absence** on the <person-m> query at 78/82. My
dispatch prompt specifically warned me not to write tooling-limited negatives up as content-exhausted
ones, and the tool enforced that for me rather than leaving it to my discipline. That is the correct
division of labour and it directly changed what I wrote (<person-m> logged as unresolved-retry, not as a
negative finding).

**Severity summary:** Friction 1 — slowed. Friction 2 — slowed, near-miss on a wrong conclusion.
Friction 3 — slowed, degraded output strength. Net: the tool won the ticket; the friction is all in
getting aggregates and in empty-result presentation.

## 2026-08-19 · brief-writing task (turned out stale) · claude-sonnet-5

**Context:** claimed a brief-writing task instructing me to write a long-form
brief on a framing correction. Did not end up
needing cascade-search at all — the actual finding was that the deliverable already
existed (fact-checked, publish-audited, with an editor since 2026-06-11), so the task closed
as a stale-ticket discovery rather than a research task. Logging this because the
absence of cascade-search usage is itself informative for triage, and because the
discovery method is a `pyrite search` / Pyrite task-list finding worth naming.

**Command:** `pyrite search "<topic keywords>" -k <drafts-kb> --limit 10`

**Expected:** a ranked list of loosely-related theme/draft entries to orient on.

**Got:** the top hit (a launch-order checklist entry) contained the exact answer in its
snippet — a highlighted `with-editor · fc complete` status line with a live shared-doc
link for the precise piece the task asked me to write. One search call resolved the
whole task. This is `pyrite search`, not cascade-search, but it is the tool that
actually mattered for this ticket and it worked well: FTS ranking surfaced a launch
checklist over dozens of theme/task files that also mention the topic keyword.

**Friction:** none in the tooling. The friction was upstream, in the task itself —
the claimed ticket's body asserted "the brief EXISTS but is not yet drafted" as
settled fact, sourced to a 2026-06-11 brief-gap survey. Two *other* task files for
the identical deliverable (two differently-slugged task files) were both already `status:
done` in the same `pyrite task list` output space, and the actual draft file had
been sitting in the drafts folder with a completed audit trail for over two months.
Nothing in `pyrite task list --status open` flagged this as likely-redundant before
I claimed it — I only found out by reading the task body, then checking the
`Produces:` path by hand.

**Would have helped:** if task claiming (or the research-worker skill's Step 1
picker) did a cheap pre-check — does the `Produces:` path, or an obvious slug match,
already exist as a file? — before surfacing a task as open. This is a Pyrite/
task-hygiene gap, not a cascade-search gap, but it cost real wall-clock: I read three
overlapping task files and a 398-line draft plus its audit sidecar before concluding
"do nothing, close it." A worker with less discipline than "verify the Produces: path
before writing" could easily have shipped a second competing draft against a piece
already sitting in an editor's queue.

**Worked well:** `pyrite search` scoped to the drafts KB surfaced the ground-truth
status in the first hit. The audit-sidecar convention (separate `.audit.yaml` with
fact_check_log / publish_audit / revision_log / pre_publish_blockers) made it fast to
confirm the draft was not a rough stub — it had already cleared fact-check and a
publish audit, which is what made "close as done, do not duplicate" the confident
right call rather than a guess.

**Severity:** slowed (on the task-hygiene issue, not on any search tool). No
cascade-search friction to report this pass — clean non-use.

## 2026-08-19 · PSC/NAICS pull task (27 awards, <vendor-l>) · claude-sonnet-5

**Context:** ticket asked for PSC + NAICS + course-title/description on all 27 of <vendor-l>'s
federal prime contracts, testing whether any repeats the <vendor-b> ICE
pattern (protective-purpose title against combat-coded PSC U013). Needed the actual
`product_or_service_code` field on every award, not just titles.

**Command 1:** `$CS --wait usaspending "<vendor-l>" --group contracts --limit 27 --json`

**Expected:** either the PSC/NAICS fields directly, or a documented way to pull them for a
whole result set.

**Got:** a clean 27-row `meta` block per award — `award_id`, `recipient`, `amount`, `agency`,
`sub_agency`, `award_type`, dates, `description` — genuinely useful, but **no PSC, no NAICS**.
The task this ticket exists to do (compare title against PSC) is structurally impossible from
this command's output alone.

**Command 2:** `$CS --wait record usaspending:307787319 --json` (one of the award `record_id`s
from Command 1's output)

**Expected:** the full FPDS `latest_transaction_contract_data` block, since the skill doc frames
`record` as "the record holds everything the API sent."

**Got:** a much thinner object than expected — `internal_id`, `Award ID`, `Recipient Name`,
`Awarding Agency`, `Awarding Sub Agency`, `Award Amount`, dates, `Description`,
`Contract Award Type`, `recipient_id`, `awarding_agency_id`, `agency_slug`,
`generated_internal_id`. Still no PSC/NAICS. This is the SEARCH-INDEX summary record, not the
FPDS transaction-contract-data object — a different, narrower thing than what the skill doc's
"the record holds everything the API sent" line led me to expect for this source.

**Friction:** the only way I found to get PSC/NAICS was to go outside cascade-search entirely:
take the *numeric* `record_id` cascade-search's `--group` output carries in `meta.record_id`
(e.g. `usaspending:307787319` → `307787319`), then hit
`https://api.usaspending.gov/api/v2/awards/307787319/` directly with `curl`, which returns the
full `latest_transaction_contract_data` block including `product_or_service_code`,
`product_or_service_description`, `naics`, `naics_description`. That endpoint takes the same
numeric id `--group` already gives you, which makes this feel like a client gap rather than a
missing capability of the underlying API — the data is one hop away and cascade-search doesn't
make that hop. Did this for all 27 awards (a `while read` loop over the 27 ids with `sleep 0.6`
between calls), successfully, but entirely outside the tool.

**Would have helped:** a `--full` or `--detail` flag on `usaspending`, or on `record` when
`--source usaspending`, that follows through to the awards-detail endpoint and returns PSC/NAICS/
full transaction-contract-data — the exact fields a procurement-classification comparison needs
and the exact fields a prior ticket on this same beat (the ICE <vendor-b> award,
`70CDCR21P00000057`) had to get the same way, by a worker hand-querying the raw API. Two workers
now have independently reimplemented "hit the awards-detail endpoint by numeric id" outside the
tool for the identical class of question. This is a good candidate for a first-class command:
`cascade-search usaspending-detail <award_id>` or similar.

**Worked well:** the `--group contracts --limit 27` call was fast, complete (all 27 in one
`Hit`, no pagination fighting), and the `description` field it does carry was accurate and
useful for the "is there a protective-purpose title" half of the question — I just had to pair
it with data pulled elsewhere for the PSC half. `usaspending --count` also gave a clean, trusted
`27` up front before I did anything else, which is exactly the confirm-the-total step the skill
doc recommends.

**Severity:** slowed — not blocked, since the direct-API workaround is reliable and fast (27
awards in well under a minute including sleep spacing), but it is a silent gap: nothing in
`--help` or the skill doc flags that PSC/NAICS require leaving the tool, so the first attempt
(via `record`) cost a full round-trip before I found the actual path.
`[fixed 887fe54 — `usaspending --detail <id>` returns PSC and NAICS directly; you were the third worker to hand-roll the same curl loop, which is what made the case. The very next opus-5 pass used it and reported it carried that task's central finding: PSC codes 6910 TRAINING AIDS / U099 contradicted a vendor's own marketing and corrected a standing research judgment. This is the clearest complaint-to-shipped-feature loop in the file.]`

---

## 2026-08-19 · trade-press ownership task (<vendor-d>/<outlet-a>, sourcing-bias methodology) · claude-opus-5[1m]

**Context:** verify at tier-1 that <outlet-a> is owned by <vendor-d>, establish what <vendor-d> sells,
and determine whether the ownership skews the vendor census. Heavy use of `usaspending`
(`--count`/`--sum`/`--detail`), `extract`, `courtlistener`, `news`, `web`.

**Headline: `--detail` and `--sum` both landed, and both did exactly what the prior entry in
this file asked for.** I read the entry immediately above mine — a worker hand-rolling
`curl https://api.usaspending.gov/api/v2/awards/<id>/` in a `while read` loop to get PSC/NAICS
because the client wouldn't make the hop. I ran `--detail` on ten awards and it returned
`PSC T013: PHOTO/MAP/PRINT/PUBLICATION- TECHNICAL WRITING | NAICS 561410: DOCUMENT PREPARATION
SERVICES` etc. directly. **That one flag carried my entire central finding**: <vendor-d>'s public
self-description is "policy, training, wellness" SaaS, and the corpus had previously written it
off as "not a training vendor" on that basis — the PSC codes (four × `6910 TRAINING AIDS`, one
`U099 EDUCATION/TRAINING`, one under `NAICS 611699`) contradict the marketing and corrected an
existing research judgment. The skill doc's claim that these codes are "the contracting officer's
classification, not the vendor's marketing" is not a nice-to-have framing; it was the load-
bearing evidentiary move of the whole task. Same for `--sum` returning `$1,564,745.51 across 15
awards` with the literal word **`complete`** — I could state the figure as exact rather than a
floor, which is a claim I could not have made a day ago. The loop from complaint to shipped
feature is visibly closing in this file, and it changed what I was able to assert.

**Friction 1 — `--detail` ignores the query argument, which is positionally required.**
Command as run:
```
$CS --wait usaspending "<vendor-d>" --detail 277741113
```
That worked. But when I wanted detail on seven more awards I had already collected ids for, I
did not want to retype the vendor name, and `--detail` clearly doesn't need it — the record id
fully determines the record. I guessed it was still required and passed a throwaway:
```
$CS --wait usaspending "L" --detail 277742330
```
This returned the correct <vendor-d> award. So the query string is accepted and **silently
ignored** when `--detail` is present. That's benign here but it's a trap: nothing stops
`usaspending "<vendor-b>" --detail 277742330` from confidently returning a *<vendor-d>* record
under a <vendor-b> query, and a worker skimming output would have no signal. Either make the
positional optional when `--detail` is given, or error if both are supplied. *Observed:* passing
`"L"` returned the right record. *Concluded (may be wrong):* the query is discarded rather than
cross-checked.
**Severity:** annoyed, with a latent correctness hazard.
`[fixed 6103d1c — the positional is optional under --detail, and supplying both cross-checks: a query that does not appear in the returned record prints a NOTE naming both. The hazard you predicted (`"<vendor-b>" --detail <a-<vendor-d>-id>` returning <vendor-d> under a <vendor-b> query) is the exact case it now catches.]`

**Friction 2 — `courtlistener` `total_matches` is not in the human-readable output, and I had
to write Python to find it.** The skill doc is emphatic: "Counting filings? Quote the phrase,
and read `total_matches`… '*at least 20*' is almost never the honest answer." Good advice, and I
wanted to follow it. But:
```
$CS --wait courtlistener '"<vendor-d>"' --type r
```
prints `results:  20` and twenty rows. No total. So I went to `--json`, and my first attempt
guessed wrong about the shape:
```
$CS --wait --json courtlistener '"<vendor-d>"' --type r | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('meta',{}),indent=1)[:800])"
```
**Got:** `{}` — a bare empty dict, with no indication whether `meta` was absent, empty, or
somewhere else. I had to write a recursive walker over the whole JSON to locate it at
`results[0].meta.total_matches = 293`. It is nested *inside the first result*, not at the top
level where "read `total_matches`" reads like it should be.
**Would have helped:** print it in the human output — `results: 20 of 293 total_matches` — which
is one line and would make the doc's advice followable without touching `--json` at all. Failing
that, the skill doc should say *where* the field lives, because "read `total_matches`" implies a
top-level field and it is not one. This is the doc's single most-repeated numeric caution and
the field is the hardest one in the tool to actually get to.
**Severity:** slowed.
`[fixed ec88f4c — the human header now reads `results: 20 of 293 total_matches (this page only -- cite 293, not 20)` on every source reporting a total. Your framing decided the fix: the count the doc leans on hardest should not be the field hardest to reach. Independently reported in the vendor-market census entry above — two reports, one line of output.]`

**Friction 3 — `extract` truncates by default and the cap costs a round-trip.** Command:
```
$CS --wait extract "https://<vendor-d-domain>/about-us/" --text
```
The reduction was superb (110,323 → 1,783 tok, 98.4%) but ended in
`[TRUNCATED for display: 6,823 chars total, 2,823 not shown. Use --json for the full text, or
--max-chars N to raise this cap.]`. The message is clear and names both fixes — genuinely good
error copy. But the default cut 2,823 chars out of an already-98%-reduced 6,823-char document,
and the tail I lost turned out to contain the **single most load-bearing string in my whole
task**: the product menu listing "<vendor-d-product>" as a <vendor-d> *app* alongside "<outlet-a>" as a
*resource*, which is what proves the news brand and the training product are the same brand
family. I only got it because I re-ran with `--max-chars 12000` on a hunch.
**Would have helped:** scale the display cap to the *post*-extraction size rather than a fixed
char count. When extraction has already achieved 98% reduction, truncating the survivor is
working against the tool's own value proposition — the whole point is that the extracted text is
small enough to read. A ~7k-char extraction should just print.
**Severity:** slowed, and this one nearly cost me the finding.
`[tracked — agreed and unfixed. Scaling the display cap to the POST-extraction size is the right shape: truncating a survivor that extraction already reduced 98% works against the tool's own value proposition. Holding only because the cap interacts with the OCR and --json paths and deserves its own pass, not because the argument is in doubt. Highest-value open item.]`

**Friction 4 — no way to ask "does site X cover topic Y", which is a real shape of question on
this beat.** I needed to test whether <outlet-a> under-covers its owner's competitors. Ran:
```
$CS --wait web '"<vendor-a>" site:<outlet-a-domain>'
$CS --wait web '"<vendor-b>" site:<outlet-a-domain>'
```
Both returned `RateLimited` (coverage 78/82; `RATE-LIMITED: searxng:brave, searxng:google cse |
ERRORED: searxng:duckduckgo, searxng:startpage`). **The typed outcome did its job perfectly and
I want to be clear that this is not a bug** — it correctly refused to let me write a
tooling-limited negative as a finding, which is precisely the failure the tool exists to
prevent, and I reported "not determinable" in my artifact instead of a fake absence. That is the
system working.

But the *capability* gap is real and worth logging as a missing-source finding per the doc's
"Missing sources are findings" note: **there is no source in the tool that can answer "how much
does publication X write about entity Y."** I tried the obvious fallback — the publication's own
search — and it is a trap:
`https://<outlet-a-domain>/search?q=<vendor-a>` → **1,967 results**;
`?q=<vendor-b-full>` → **28,081**; `?q=<vendor-l-brand-1>` → **41,130**.
Those numbers are worthless. The site tokenises unquoted terms, so they measure how often
the individual common words in each name appear across the archive — the exact error class the skill doc
warns about for unquoted CourtListener queries (`<vendor-b-full>` 51,622 vs `"<vendor-b-full>"`
155), reproduced on a different corpus. **I flagged this in my research notes
specifically so the next worker doesn't mistake them for a coverage census**, but a less careful
pass would have published "<outlet-a> mentions <vendor-b> 28,081 times" as a finding, and it
would have been meaningless.
**Would have helped:** either a `--site` flag on `web` that routes to an engine known to honour
site-scoping, or — better and more honest — a documented note that publication-coverage counts
are **out of scope**, so workers stop reaching for the site-search trap. Media-ownership and
coverage-bias questions are recurring on this beat and the tool currently has no honest path to
them.
**Severity:** annoyed (I got a defensible "not determinable" out of it, which is the right
answer, but only because I knew to distrust the site-search numbers).
`[wontfix as a feature — tracked as a documented boundary. "How much does publication X write about entity Y" has no honest path here: site-scoped engine counts are estimates that move between runs, and a publication's own search tokenises (your 1,967 / 28,081 / 41,130 are counts of the individual common words in each name). Both roads produce a number that looks like a census and is not one, which is the failure class this tool exists to prevent — so the tool should keep declining rather than ship a plausible fake. Recording it as out-of-scope in the skill doc so the next worker stops reaching for the site-search trap; you already flagged it in the research notes, which is the correct handling.]`

**Friction 5 — `AccessBlocker: http-404` on a guessed URL reads like a wall, but is just a
wrong guess.** Commands:
```
$CS --wait extract "https://<outlet-a-domain>/about" --grep ...        → AccessBlocker, http-404
$CS --wait extract "https://<outlet-a-domain>/editorial-standards" ... → AccessBlocker, http-404
$CS --wait extract "https://www.<outlet-b-domain>/about-us" --grep ... → AccessBlocker, http-404
```
Each printed `NOT a negative finding. Access was blocked.` That's true but misleading in
register: 404 is not "access was blocked," it's "you guessed the wrong path." Nothing is walling
me. The framing pushed me briefly toward "<outlet-a> is blocking the about page," which would have
been a *spicy and completely false* addition to a media-conflict writeup — exactly the kind of
overstatement my ticket warned against. `/about-us` worked fine on the next try.
**Would have helped:** give 404/410 their own outcome line — `NotFound` — or at minimum change
the copy to "the URL does not exist; check the path" rather than the anti-blocker language,
which should be reserved for Cloudflare/Turnstile/403.
**Severity:** annoyed, with a real overclaim hazard on exactly this beat.
`[fixed ec88f4c — 404/410 no longer print the anti-blocker language. They now read "NOT a negative finding, and NOT a block -- the URL does not exist", name the path-guess case, and suppress ESCALATABLE. Exit code is deliberately unchanged (still 2, never a verified absence). Reported independently by tick7-b during a sequential-id sweep; you called the register mismatch and the overclaim hazard exactly right.]`

**Friction 6 — GlobeNewswire `extract` times out repeatedly (reported, not blocking).**
```
$CS --wait extract "https://www.globenewswire.com/news-release/2019/02/08/<id>/0/en/<vendor-d>-and-<vendor-q>-Merge-<rest-of-slug>.html" --text --max-chars 6000
```
**Got, twice, ~4 minutes apart:**
```
== RateLimited ==
retry after: 30s
detail:    timeout: The read operation timed out
```
Two notes. (a) Classifying a **read timeout** as `RateLimited` conflates "the server was slow"
with "you queried too fast" — different causes, different retry strategies; the `detail` line
carries the truth but the outcome label misdirects. (b) `retry after: 30s` was not accurate; I
waited far longer and got the same result. Not blocking — the tier-1 fact was available from the
subject's own site — but wire-service press releases are a standard tier-1 corroboration source
on this beat and GlobeNewswire being effectively unreadable is a gap worth knowing about.
**Severity:** annoyed.
`[tracked — both halves accepted. A read timeout is not a throttle: same label, different cause, different retry strategy, and the advertised `retry after: 30s` was simply wrong for it. Wants its own outcome (or at minimum a timeout-specific detail line and no fabricated interval) rather than being folded into RateLimited. Unfixed.]`

**Worked well — specifically:**
- **`--detail`**, as above. Carried the task's central finding and closed a loop opened in this
  very file. This is the feature that made the difference between "<vendor-d> is a SaaS company per
  its website" and "the government classifies <vendor-d> as a training school and a publisher."
- **`--sum`'s `complete` vs floor marker.** Being told the figure is complete let me upgrade an
  existing research-notes number from approximate to exact and say so.
- **`extract --grep` returning an explicit in-document negative.** This produced my single
  sharpest finding. Running
  `$CS --wait extract "https://<outlet-a-domain>/about-us" --grep "<vendor-d>|<VENDOR-D>|parent|owned|ownership|subsidiary|<vendor-q>"`
  returned
  `NO MATCHES for '…' in 37,547 tokens of text. This is an absence IN THIS DOCUMENT ONLY -- the
  document was read, the terms are not in it.`
  That phrasing is exactly right and I quoted its logic straight into my artifact: <outlet-a>'s
  disclosure page details ad and affiliate revenue and never names its owner. **A bare empty
  result would not have been publishable; the explicit scoped-absence language made it a
  finding.** More tools should do this.
- **`--grep` on a local PDF** (a locally archived 2009 column) using the identical syntax as on
  a URL. No mode switch, no flags to remember, and it surfaced both the `Copyright © 2026
  <vendor-d>` footer and the author's disclosed vendor affiliation in one call.
- **`usaspending --count --all-types`** giving `contracts: 15 | loans: 1` up front, so I knew the
  shape before deciding which `--group` to page.

**Overall severity:** slowed. Nothing blocked me and the task completed fully. Frictions 1, 3,
and 5 are all small and each carries a *correctness* hazard rather than merely a time cost —
they are cheap fixes with disproportionate payoff. Friction 4 is the interesting one: a genuine
missing capability on a question shape (who owns the press, and does it show in coverage) that
this beat will keep asking.

## 2026-08-19 · non-federal money tracing task (<vendor-a>, HIDTA) · claude-sonnet-5-parallel-tick7-b

**Context:** needed to (a) confirm the $3.3M <vendor-a> federal-contract floor was complete,
(b) enumerate a public vendor site's sequentially-numbered course-calendar pages
(`<vendor-a-domain>/conference/<id>/`, ids roughly 300-680) to find HIDTA/state-agency hosts and
per-student pricing, and (c) confirm HIDTA has no USAspending recipient footprint (the
structural-invisibility claim central to the task).

**Command 1:** `$CS --wait usaspending "<vendor-a>" --sum`
**Got:** exactly what I needed — `$3,298,091.75 across 52 awards *UNIQUE* ... complete`. One
call, matched a figure two prior workers had independently verified by hand-summing the award
list. This is the tool doing its job precisely as advertised. **Worked well.**

**Command 2 (the friction):** enumerating ~60 individual conference pages via
`$CS --wait extract "https://<vendor-a-domain>/conference/$id/" --grep "Hosted by" --grep
"Host:" --grep "Cost:" --grep "Class Host"`. Worked cleanly on every live page (83-88% token
reduction each), but roughly 1 in 6-8 ids in the range 300-680 returned:
```
== AccessBlocker ==
coverage: 0/0 responsive
mechanism: http-404
detail:    HTTP 404
NOT a negative finding. Access was blocked.
```
**Had to figure out:** whether "AccessBlocker" for a plain 404 on a sequential-id enumeration
sweep should be read the same way as a Cloudflare/Turnstile block. It shouldn't, functionally —
a 404 on an incrementing ID just means that ID was never issued or was later pruned, which is
useful signal (I used the 404 pattern itself to infer the site prunes old listings), not a wall
to route around. The message "NOT a negative finding. Access was blocked." is accurate for a
paywall/CAPTCHA scenario but reads as slightly alarming/misleading for a bare 404 on an
enumerable range — I want to flag this as a possible false-alarm register mismatch, not a bug:
an agent doing ID-sweep enumeration (a legitimate, useful pattern this tool otherwise supports
well via `extract`) will see "AccessBlocker" dozens of times in normal, expected operation.
**Would have helped:** distinguishing `http-404` from real access walls (Cloudflare/Turnstile/
403) more sharply in the outcome name or a one-line note like "404 on an enumerated URL is
usually just an unissued/removed ID, not a block" — so an agent doesn't second-guess a healthy
sweep pattern. Low severity; I did not misinterpret it, but I paused to check twice.

**Command 3:** `$CS --wait usaspending "High Intensity Drug Trafficking" --count`
**Expected:** either a real award count or a clear absence.
**Got:** a clean typed `VerifiedAbsence` (1/1 responsive) — exactly the bounded-negative
guarantee the skill doc promises, and exactly what let me write "HIDTA has no USAspending
recipient footprint" as a sourced claim instead of an inference. **Worked well** — this is the
single most load-bearing call in the whole task, and it did precisely what was needed in one
shot with no ambiguity.

`[fixed ec88f4c — 404 is split out of the blocker register and now names the enumeration case directly: "on an enumerated id range, a 404 usually means that id was never issued or has been pruned." You reported this as a register mismatch rather than a bug, which is what it was; the opus-5 <vendor-d> entry hit the same edge from the path-guess side. Two independent reports, one fix.]`
**Severity:** annoyed (the AccessBlocker-on-404 register issue), otherwise clean — no blockers,
no rate-limit surprises beyond the normal shared-ledger `web` RateLimited-then-retry cycle
documented elsewhere in this log, which behaved exactly as described.

## 2026-08-19 · trade-press ownership task (<vendor-d>/<outlet-a>), SECOND pass on an already-worked ticket · claude-opus-5[1m] · agent:claude-opus-4-8-parallel-tick8-a

**Context:** dispatched on the same ticket tick7-a worked earlier today, under a brief that
said it was unworked. It wasn't. So I used cascade-search for something the log doesn't have
an entry for yet: **independently reproducing another agent's tier-1 claims.** That turns out
to be a distinct use case, and the tool is unusually good at it. Reporting from that angle.

**Reproduction is where this tool shines, and I don't think that's been named yet.**

Three claims, three commands, three exact reproductions:

```
$CS extract "https://<vendor-d-domain>/about-us/" --grep "<vendor-q>|<outlet-a>|<org-d>|<org-e>|2019|acquir"
$CS usaspending "<vendor-d>" --sum
$CS extract "https://<outlet-a-domain>/about-us" --grep "<vendor-d>|parent|owned|ownership|subsidiary|<vendor-q>"
```

The `--sum` came back `$1,564,745.51 across 15 awards ... complete` — **identical to the cent**
against a figure produced hours earlier in a different session. The `--grep` on <outlet-a-domain>
returned `NO MATCHES ... in 37,547 tokens` with the same token count as the prior pass. That
byte-identical reproducibility is the property that makes a verification pass cheap, and it's
a direct consequence of extraction being deterministic regex/DOM rather than model
summarisation. **Worked well, and it's the headline:** most research tooling can't be used to
check itself, because a second call gives you a differently-worded summary and you can't tell
a real discrepancy from paraphrase drift. Here I could.

**Friction 1 — `--detail` output is so terse I couldn't tell a missing field from an absent one.**
```
$CS usaspending x --detail 307695512 2>&1 | grep -iE "psc|naics|descr|amount|recipient|agency"
```
returned exactly one line: `NAICS 561920: CONVENTION AND TRADE SHOW ORGANIZERS`. The sibling
award returned two lines, including a PSC. **Observation:** award 307695512 printed no PSC line.
**Conclusion I could not safely draw:** whether that award *has* no PSC code in USAspending, or
whether `--detail` omitted it. Those mean different things and I had to hedge my writeup
accordingly. (Noting I piped to `grep`, so I did not see the unfiltered output or the real exit
code — I'm reporting the filtered observation, not claiming a bug.) **Would have helped:** print
absent fields explicitly as `PSC: (none in record)` rather than omitting the line. Silence is
ambiguous in exactly the place where the codes are the load-bearing evidence.
**Severity:** slowed (and carries a correctness hazard — I nearly wrote "no PSC assigned").
`[fixed ec88f4c — absent fields are now NAMED: `PSC: (none in record)` rather than a dropped line. Checked against your exact award: 307695512 genuinely has no PSC, so your hedge was right and the tool was hiding the fact rather than the fact being unavailable. Your instinct to report the filtered observation without claiming a bug was the correct call, and it found a real one.]`

**Friction 2 — the same `web` rate-limit wall defeated the same question twice, and the tool
can't tell me that.** My site-scoped query returned:
```
== RateLimited ==
coverage: 78/82 responsive | RATE-LIMITED: searxng:brave, searxng:google cse
| ERRORED: searxng:duckduckgo, searxng:startpage
retry after: unspecified by the source
```
tick7-a hit **the same 78/82, the same four engines** on the same question this morning. The
typing is correct and I did not write it up as an absence — the guarantee worked exactly as
designed. But two passes burned budget discovering the same wall independently. **Would have
helped:** `retry after: unspecified by the source` is the least useful field in the output; if
the shared ledger knows a source was limited N minutes ago, say so (`brave: limited 4h ago,
still cooling`). Better still, a note when a *query shape* has recently failed this way.
**Severity:** annoyed, but it's a real budget cost multiplied across parallel workers.

**Friction 3 — CourtListener docket pages are ~95% chrome, and `--text` gives you the chrome.**
```
$CS extract "https://www.courtlistener.com/docket/<docket-id>/<case-slug>/" --text
```
94.5% reduction, and the surviving 1,510 tokens were almost entirely CourtListener's
sign-in modal, RECAP install nag, and the "🙏 daily prayers" explainer. **Zero** case metadata.
The thing I needed — `Cause: 17:501 Copyright Infringement / Nature of Suit: 820 Copyright` —
only appeared once I guessed a `--grep` for `Nature of suit|Cause of Action|...`. **Had to
figure out:** that `--text` is the wrong mode for a docket page, and which field names to grep
for. This mattered a lot: that one line **corrected a finding** the prior pass had carried
forward as "a vendor sued over the policy it drafted." It's a copyright case. **Would have
helped:** a `--docket` mode (or courtlistener-aware extraction) that pulls the metadata block —
cause, NOS, judge, filed date, party roles — since that block is the whole reason to fetch a
docket page. Given `courtlistener` is already a first-class source, `extract` not knowing its
own sibling's page shape is a gap. **Severity:** slowed; near-miss on correctness.

**Where the results fell short (not an error, a corpus point).** The ownership question —
"who owns this trade outlet" — has no good source here. I got there via `web` (which found
<outlet-b-domain>'s own announcement) plus `<org-f-domain>/about`, then *validated* the answer with
`usaspending --limit/--detail` on the publisher. **That last move is the generalizable trick and
I'd like it written down somewhere:** to test whether a publisher is a market participant, pull
its federal award PSC/NAICS. <org-f> came back `PSC U005 TUITION/REG/MEMB FEES, NAICS 561920
CONVENTION AND TRADE SHOW ORGANIZERS`; <vendor-d> came back technical writing, training aids, and
misc-schools. The contracting officer's classification separated a press outlet from a vendor
that owns one, on a question procurement data isn't nominally *for*. That's the strongest thing
the tool did for me today and it wasn't in any doc.

**Worked well, specifically:**
- `--sum` reporting `complete` vs. floor. I could state a figure as exact instead of hedging.
- The in-document absence message (`This is an absence IN THIS DOCUMENT ONLY -- the document
  was read, the terms are not in it`) is the single best-worded output in this tool. It states
  the scope of the negative so precisely that I could put it in the research notes verbatim as evidence.
- `RateLimited` refusing to masquerade as absence, twice, on a question where a false negative
  would have been an actual published error.

**Overall severity:** slowed. Task completed, nothing blocked. Friction 3 is the one worth
fixing first — it sits on a first-class source and it nearly let a mischaracterised case stand.

## 2026-08-19 · lead-corroboration task (county commission vote) · claude-sonnet-5

**[tracked]** Web/SearXNG rate-limiting cluster, the most-reported friction in this log. Partly root-caused 2026-08-30: the SearXNG daemon was running against a deleted settings path, and four default general engines are durably blocked. Re-reports add corroboration, not new signal.

**Task:** corroborate a single-tier-2-sourced news lead (a <county-c>
3-1 vote to drop ICE from the jail's USMS contract) with a second, ideally tier-1, source.

**Command:** `$CS web "site:<county-c-domain> commissioners meeting April 2026 ICE" --wait`
**Expected:** either results or a clean `RateLimited`/`AccessBlocker` typed outcome.
**Got:** `RateLimited`, exit 3, with a well-formed message: "Cannot certify absence: coverage
incomplete (78/82 responsive | RATE-LIMITED: searxng:brave, searxng:google cse | ERRORED:
searxng:duckduckgo, searxng:startpage)." This is a genuinely good error — it told me exactly
what NOT to conclude. No complaint about this one; noting it because it's the mechanism working
as designed and I want that on record too. Retried the same query shape 5 seconds later
(un-timed manual retry, not a `sleep`) and it went through clean. **Would have helped:** the
tool already tells you it's rate-limited; it could optionally suggest a retry-after estimate
even when the upstream doesn't supply one (e.g. "no retry-after given; other `web` calls in
this session succeeded within ~5s, try again shortly") so an agent doesn't have to guess whether
5s or 30s is the right wait. Minor — I guessed right this time. **Severity:** annoyed.

**Friction — Legistar / government meeting-portal JS rendering.** `$CS extract
"https://<county-c>.legistar.com/MainBody.aspx" --text` and the sibling `Calendar.aspx`
and `Legislation.aspx` pages all returned only the page chrome (nav, filter-dropdown labels,
"select" placeholder text) — the actual meeting/legislation rows are loaded via AJAX and never
appear in the static HTML. This is the exact source type the task asked me to check (county
commission minutes/vote record — the task description explicitly called county minutes "usually
published" and tier-1). Legistar/Granicus is an extremely common vendor for county- and
city-government meeting records across the US, not a one-off site, so this is likely to recur
on future local-government corroboration tasks. **Had to figure out:** that the empty-looking
result was a JS-rendering gap and not a real "nothing here" — nothing in the `extract` output
flagged this as a JS-only page (contrast with the `AccessBlocker` / `http-404` typed outcome I
got on a different guessed URL on the same site, which WAS clearly labeled). I only caught it
because the extracted text was suspiciously short and all UI-chrome, no data rows — an agent
skimming faster could plausibly mistake "no results shown" for "no such meeting," which would be
a false negative on a primary-source check. **Would have helped:** `extract` detecting a
JS-shell page (e.g., body text under some token threshold with no matching content, combined
with known JS-app signatures/frameworks in the HTML) and returning an `AccessBlocker`-style typed
outcome (`mechanism: js-only` — this mechanism is already listed in the skill docs as a category
`cascade-search`'s browser tier is supposed to escalate to, but static `extract` didn't flag it
for escalation here) rather than a silent low-content `Hit`. I worked around it by falling back
to `web` search to find secondary reporting of the same meeting (a YouTube upload of the meeting
recording, confirming the date) instead of the primary Legistar record — a workaround I
documented explicitly in the research notes rather than silently treating it as verified, but it's
exactly the kind of routing-around the hallway-testing skill flags as the expensive failure
mode if it goes unreported. **Severity:** slowed (did not block — task still closed `done` with
strong tier-1 news corroboration — but the county's own primary record, which is what the task
specifically asked for, remains unretrieved).

**Worked well:** `extract --text` on live news articles was excellent — six separate local-outlet
pages each reduced
95-99% while preserving every fact I needed (commissioner names, vote tallies, dollar figures,
direct quotes) without a single re-fetch. The `news` RSS command's "redirect only, not citable"
labeling on every Google News result is a good guardrail — it stopped me from accidentally
citing an uncitable redirect URL and pushed me to `web` for a citable link, which is exactly the
right nudge.

## 2026-08-19 · source-characterization task (a detention-beat newsletter) · claude-opus-4-8-parallel-tick8-b

**[tracked]** Web/SearXNG rate-limiting cluster, the most-reported friction in this log. Partly root-caused 2026-08-30: the SearXNG daemon was running against a deleted settings path, and four default general engines are durably blocked. Re-reports add corroboration, not new signal.

Source-characterization task: decide whether a detention-beat Substack (<outlet-c>)
deserves lead-source or citation-source status. The decisive evidence came from
`usaspending`, and the tool performed extremely well on the core job. Three frictions, one
of them a genuine near-miss.

**Friction 1 — `--detail` silently requires the positional `query` it does not use.**
**Command:** `$CS --wait usaspending --detail 352043007`
**Expected:** detail on record 352043007. The record ID is globally unique and is printed
by the tool's own search output as `https://www.usaspending.gov/award/352043007`; there is
nothing a query would disambiguate.
**Got:**
```
usage: cascade-search usaspending [-h] [--detail RECORD_ID] [--sum] ...
cascade-search usaspending: error: the following arguments are required: query
```
**Friction:** I worked around it with
`$CS --wait usaspending "<vendor-f> LLC" --detail 352043007` — and the output
header then reads `query: 352043007`, i.e. **the positional query I was forced to supply was
discarded and the record ID substituted**. So the argument is required by the parser and
ignored by the logic. That's the tell that it's a parser artifact, not a real requirement.
**Would have helped:** make `query` optional when `--detail` is present. Failing that, the
error should say "`--detail` still requires a positional query (any string; it is ignored)"
— because the current message sends you looking for the *right* query when no query is
right. **Severity:** annoyed (10 seconds), but it's a two-line argparse fix and it fires on
every `--detail` call, which is the command you reach for at exactly the moment you have a
record ID and nothing else.

**Friction 2 — `--keywords` reports a match count that hides which entity matched.** This
is the near-miss and the most important item here.
**Command:** `$CS --wait usaspending "W9124J24C0019" --keywords --count --all-types`
**Got:** `1 federal awards matching 'W9124J24C0019' | contracts: 1`
I was verifying a contract ID that a Substack article attributed to <vendor-f>
LLC (a $1.3B <site-a> detention contract). **A count of 1 reads as confirmation.** It was
not. Re-running as `--keywords --limit 5` returned:
```
<VENDOR-R> — W9124J24C0019
$3,972,528 | Department of Defense | DEFINITIVE CONTRACT
```
Different company, wrong by ~330x. The article's claim was false, and `--count` alone would
have let me record it as verified.
**Friction:** for an *identifier* lookup, the count is nearly meaningless — an award ID
either exists or doesn't, and the whole question is *whose it is*. The docs' guidance
("know which sources can count", `--keywords` is fuzzy) is about **totals being fuzzy**; it
doesn't warn about the distinct hazard that **a precise-looking count of 1 on an identifier
still tells you nothing about attribution**.
**Would have helped:** when `--count` matches a small number (≤3) of records, print the
recipient names inline — `contracts: 1 (<VENDOR-R>)`. That one
change turns a misleading confirmation into an instant refutation at zero extra API cost.
Alternatively, detect that the query looks like an award/PIID identifier and route to the
listing rather than the count. **Severity:** slowed for me (I caught it because I was
already suspicious), but the failure mode is *confidently wrong*, which the FEEDBACK.md
entry above about `extract --ids` flags as this tool's characteristic hazard. Same shape.

**Friction 3 — `web` rate-limits two engines on essentially every call.** Every one of my
five `web` calls, all with `--wait`, returned:
`coverage: 79/82 responsive | RATE-LIMITED: searxng:brave, searxng:google cse | ERRORED: searxng:startpage`
**Friction:** the same three engines, every time, from the first call of the session — so
this isn't my pacing, and `--wait` doesn't touch it. It didn't hurt me (79/82 is plenty for
discovery, and I wasn't claiming absence), but it means **`web` cannot currently produce a
clean `VerifiedAbsence`** in this environment: the docs say a verified absence can't be
constructed when any engine was rate-limited or errored, and three always are. That's worth
knowing as a standing environmental fact rather than a per-run surprise.
**Would have helped:** either drop the three consistently-failing engines from the default
pool, or note in `limits` output that they are persistently unavailable so an agent doesn't
read the warning as a transient condition worth retrying. **Severity:** annoyed / cosmetic
for discovery work; **blocked** for any task whose deliverable is a `web`-based verified
absence.

**Worked well — and this one carried the whole task.**

`usaspending --sum` is the reason this task reached a defensible conclusion instead of a
hedge. The claim under test was a "$1.3 billion" single award. One command:
```
$CS --wait usaspending "<vendor-f> LLC" --sum
→ $915,073,700.81 across 33 awards ... complete
```
A $1.3B award cannot exist inside a $915M lifetime total, and **the `complete` marker is
what made that a finding rather than a suspicion** — without it I'd have had to treat $915M
as a possible floor and the refutation would have collapsed. That "complete vs. floor"
distinction is doing exactly the work it was designed to do. Please don't lose it.

`--detail`'s PSC/NAICS output was the single best artifact of the pass. For the real award:
```
PSC R706: SUPPORT- MANAGEMENT: LOGISTICS SUPPORT | NAICS 541614: PROCESS, PHYSICAL
DISTRIBUTION, AND LOGISTICS CONSULTING SERVICES
```
That is a 5,000-bed detention facility booked as logistics consulting, stated in the
contracting officer's own coding. The skill docs promise exactly this ("the strongest
evidence on this beat because they are the contracting officer's classification, not the
vendor's marketing") and it delivered precisely as advertised — I ended up citing the PSC
code as *stronger* evidence for the underlying thesis than the article that prompted the
check. Worth noting because it's the case where the tool didn't just verify a claim, it
produced a better version of it.

`extract --text` reductions were consistently 96.9–98.4% on Substack pages (55K→1.7K,
82K→1.3K, 49K→1.0K tokens) with nothing load-bearing lost. Substack is heavy chrome and
this handled it cleanly. Two specific wins: it preserved the **inline citation links**
inside the article body (a state news site, Reuters), which is how I established the piece
was a downstream rewrite rather than original reporting — a summarizer would have dropped
those. And on the outlet's About page it faithfully preserved the literal string
`[Image Placeholder: A compelling visual representing themes of justice...]`, i.e. unedited
AI-template boilerplate left live in the published page. That verbatim artifact became a
load-bearing fact in the characterization. **Deterministic extraction beat summarization
precisely because it doesn't tidy.** Worth saying out loud, since "it preserves ugly
things" is easy to mistake for a defect.

The `*UNIQUE*` marker earned its keep: the single most important lead in the whole task —
`<outlet-e-domain>/p/<post-slug>`, which carries the outlet's own
"not verified facts or legal statements" disclaimer — surfaced as a `*UNIQUE*` result at
position 4 on one engine. Consensus ranking would have buried it, and it's the piece of
evidence that settled the question in the outlet's own words.

**Missing-source note (per the docs' "missing sources are findings"):** this task needed to
establish *publication chronology* — who published a claim first, the outlet or the wires.
I assembled it by hand from `web` result snippets and article datelines, which is slow and
error-prone. A date-filtered news/archive query (`--from` / `--to` on `news`, or anything
touching a wire archive) would be a real addition for this beat, since "was this outlet
early or downstream?" is the recurring question in every source-characterization pass, and
it's the question I most had to answer by eyeball.

## 2026-08-19 · figure-verification task (a surveillance-spending total) · claude-sonnet-5

**Command:** `cascade-search --wait usaspending "<vendor-g>" --sum --from 2025-10-01 --to 2026-08-19`
**Expected:** total award value for <vendor-g> awards with action dates in the given FY2026 window.
**Got:** `$2,764,141,065.30 across 280 awards | complete` — the exact same figure and award count
as running `--sum` on the same query with no date flags at all. Re-ran it twice (including
with `--max-pages 5`) to rule out a fluke; identical both times.
**Friction 1 — `--sum` silently ignores `--from`/`--to`.** This is not a partial-sum or
rate-limit downgrade (it self-reports "complete"), it's a confidently wrong scope. I only
caught it because I cross-checked against `--count` on the identical query+date-range, which
DID respect the bound correctly (87 awards in-window vs. 280 all-time) — if I hadn't run
that comparison I would have reported an all-time, all-agency total as a single-fiscal-year
figure with a straight face. **Severity: slowed**, but only because I happened to
cross-check; for a worker that trusts `--sum`'s own "complete" label, this is a
confident-wrong-answer hazard exactly like the OCR digit-confusion warning already in the
docs, and it doesn't carry an analogous warning.
**Friction 2 — no agency/sub-agency filter on `usaspending`.** The task was to verify an
ICE+CBP-specific spend figure. Recipient-name search returns awards across every agency
(<vendor-h> alone: DoD, VA, USDA, DHS all mixed in one result list), and the finest filter
available is `--from`/`--to` on date — nothing scopes to "DHS" let alone "ICE" or "CBP"
specifically. I worked around it by eyeballing the `| Department of X |` field in each
result row and hand-summing the DHS-tagged ones, which is exactly the kind of hand-scrape
the skill doc warns against doing for a *total* — I did it anyway because there was no other
way to isolate the subset, and I flagged in the artifact that the resulting number is
therefore a floor/approximation, not a verified total. An `--agency` or `--sub-agency` flag
(even just toplevel department, which is already present in the row output) would remove
the need for that workaround and make the resulting total citable rather than hedged.
**Would have helped:** (1) either make `--sum` respect `--from`/`--to`, or have it refuse/warn
when a date range is passed but not applied — silent-and-confident is the worst combination;
(2) an `--agency` filter on `usaspending` list/count/sum, since "how much did agency X spend
with vendor Y in fiscal year Z" is close to the modal question on this beat and currently
requires a hand-scrape workaround that the tool otherwise explicitly warns against.
**Worked well:** `--detail` on individual award IDs was exactly as advertised — pulled PSC
`Y1BG` "Construction of Electronic and Communications Facilities" on an <vendor-g> DHS award and
PSC `DA01` "IT/Business Application Development Support Services" on a <vendor-h> DHS award,
both fast, both precise, both load-bearing for distinguishing surveillance-infrastructure
procurement from generic IT spend without having to read a single procurement document.
`extract --grep` on the NPR article (46K→~3 tokens) was the single most decisive step in the
whole task: it definitively established that a news lead's cited tier-1 source did not
contain the dollar figure attributed to it, which is a materially different and more useful
finding than "the web doesn't discuss X" — deterministic grep against the full text is what
made that a fact rather than a guess.

## 2026-08-19 · verification sweep over near-publication pieces · claude-sonnet-5

**Command:** `cascade-search usaspending "<vendor-e-parent>" --sum --wait`
**Expected:** either a distinct-entity total for the legal/DBA name "<vendor-e-parent>," or a clear
signal that this is a keyword match rather than a recipient-name match.
**Got:** `$3,654,682,628.40 across 283 awards | complete` — presented identically to a clean
recipient-name total, no different from `usaspending "<vendor-e>" --sum`. Nothing in the
output distinguishes "this is a real distinct recipient" from "this is a keyword hit whose
underlying awards belong to a recipient you already queried under a different name."

**Friction — a keyword match that looks exactly like a recipient-name match is a real trap,
and I nearly walked into it.** The task brief I was given (correctly) warned that keyword
counts aren't vendor totals, but the way I actually caught this wasn't the warning — it was
noticing that `usaspending "<vendor-e>" --sum` ($7.08B) plus `usaspending "<vendor-e-parent>"
--sum` ($3.65B) landed suspiciously close to a flagged $10.7B figure I was trying to
resolve, which made me suspicious enough to pull `--limit 5` on both and eyeball the PIIDs.
Two award numbers (SAQMMA16F4675, 19AQMM22F0469) appeared in *both* result sets, both
recipient "<VENDOR-E> INC" — i.e. "<vendor-e-parent>" is not a second entity, it's a keyword
that happens to also match <vendor-e>'s own contract records (<vendor-e> is a
<vendor-e-parent> subsidiary and its records likely mention the parent). Had I not cross-checked,
I would have reported $7.08B + $3.65B = $10.7B as a verified two-entity total, which is
*exactly* the error the flagged figure I was investigating appears to have made in the
first place — this may be how that $10.7B number got constructed originally. **Severity:
slowed** (caught it myself this time) but this is a confident-wrong-answer hazard on the
order of the OCR digit-confusion warning: two independently-run `--sum` calls that look
equally authoritative, one of which is silently double-counting the other.
**Would have helped:** any signal in `--sum`/`--limit` output distinguishing a search where
100% (or some high fraction) of returned awards share a recipient name already seen under a
different query in the same session — even just printing the distinct recipient names
found, the way `--detail` already surfaces PSC/NAICS, would have let me catch this in one
call instead of three. Short of that, the docs' existing "usaspending recipient matching is
precise on CONTRACTS, fuzzy on grants/loans" caution should be broadened: it's also fuzzy
between a subsidiary and a parent-company keyword, on contracts, when the parent name isn't
itself a recipient of record.
`[fixed eef4697 — a --sum/--count whose awards resolve to recipient names other than the one asked for now prints the distinct names found and warns explicitly not to add the total to another vendor's without checking shared award IDs. Verified live on `usaspending "<vendor-h> Technologies" --sum`, which surfaces <VENDOR-H> TECHNOLOGIES INC. + <VENDOR-H> USG INC. Your catch is quoted in the warning text: the $10.7B double-count is named as the reason it exists.]`

**Command:** `cascade-search usaspending --detail 291199463 --wait`
**Expected:** enough detail to confirm which specific program/system an ICE-<vendor-h> award
funds (I needed to know whether a Sept 2025 $29.9M transaction was a new contract or a
modification to an existing one).
**Got:** one line — PSC and NAICS only (`PSC DA01: IT AND TELECOM - BUSINESS APPLICATION/...
| NAICS 511210: SOFTWARE PUBLISHERS`). No award description, no transaction history, no
modification list, no awarding sub-agency name.
**Friction — `--detail` didn't answer the question it was closest to answering.** I had to
drop to the raw `api.usaspending.gov/api/v2/awards/<id>/` and
`api.usaspending.gov/api/v2/search/spending_by_transaction/` endpoints directly via curl to
get the award `description` field (which is where "<PROGRAM NAME>
OPERATIONS AND MAINTENANCE..." actually lives) and the per-transaction modification history
(which is where I found the $29.9M Sept 25 2025 line item and confirmed it was a
modification to the *same* award, not a new one). This was the single most load-bearing
check in the whole task — it's what let me catch a piece asserting two dollar figures that
were actually the same contract — and cascade-search's `--detail` didn't reach it.
**Would have helped:** `--detail` returning the award `description` field (one string,
already in the API response I pulled) would cover most of this. A `--transactions` or
`--modifications` flag surfacing the transaction-level list (date, amount, description) for
one award ID would cover the rest, and directly serves this beat's recurring "is this a new
contract or a bigger bite of an old one" question — the same shape of question as the
<vendor-g>/`<vendor-b>`/etc. keyword-vs-name traps already documented above, just one level
down at the transaction layer instead of the recipient layer.
`[tracked — accepted, unfixed, and the strongest open feature request in this file. --detail returns PSC/NAICS only; the award `description` field is already in the response it parses and would answer most of this for free. The transaction-level list (--transactions) is the other half and serves a recurring question on this beat: is this a new contract or a bigger bite of an old one. You had to drop to raw curl for the single most load-bearing check in the task.]`

**Command:** `cascade-search usaspending "<VENDOR-S>" --sum --wait`
(repeated ~12 times over roughly 5 minutes)
**Got:** `RateLimited` every time, with the detail line showing a *different* partial-sum
progress each retry (500 awards/5 pages, then 700/7, then 0/0, then 800/8...) — i.e. another
worker or workers were actively running large `--sum` sweeps against usaspending
concurrently and eating the shared 30-calls/60s budget before my paging could complete. This
matches the doc's "rate limits are shared across workers" warning exactly, and the doc's
guidance (retry, don't report as absence) was correct and is what I did — eventually got a
clean `complete` result once traffic died down. **Not a tool bug**, flagging as confirmation
that the shared-budget contention described in the docs is a real, current, and non-trivial
source of delay during a busy multi-worker tick (this cost roughly 5 minutes of wall time on
one query), not just a theoretical edge case.
`[tracked — confirmed, and now the practical ceiling on parallel ticks: usaspending is 305 of 552 logged calls, so `--sum` sweeps are what workers contend over. Correct behaviour (a shared ledger is the point — it is what stops workers from collectively burning a budget none of them can see), but the retry currently gives back no way to distinguish "briefly contended" from "hopeless for the next ten minutes." Worth a queue position or an observed-wait estimate rather than a bare retry.]`

**Worked well:** `--from`/`--to` on `usaspending --sum` is fixed and I confirmed it live —
`usaspending "<vendor-e>" --sum --from 2020-01-01 --to 2022-12-31` correctly returned a
smaller bounded total ($4.84B/304 awards) than the all-time total ($7.08B/480 awards) and
still self-reported `complete`. (A prior 2026-08-19 entry in this same log reported this flag
silently ignored on an <vendor-g> query — worth re-testing that exact query if anyone wants to
confirm the fix generalizes, since my one confirmation used a different vendor.) Also: the
plain `--sum`/`--limit` combination on `usaspending` is what actually caught two separate
wrong-figure findings in this task (a disputed $10.7B aggregate, an overstated $30M-plus-
$139.3M framing that was really one $150.7M contract) — the tool did its job well once I
knew to cross-check keyword hits against recipient-name hits by PIID.

---

## 2026-08-19 — conductor tick 9, absorbing five parallel workers (opus-5)

**Bug found in the field, and it is the exact failure this tool exists to prevent.**
While QC-checking a worker's claim that DHS delivery order `70CDCR26FR0000001`
was worth $598,444,871, I ran `usaspending --detail 70CDCR26FR0000001` to verify
independently. It returned **VerifiedAbsence** — a *publishable negative* — on an
award that plainly exists and that `usaspending "<vendor-f>"` returns
as its first result.

The cause: `/awards/<id>/` keys on USAspending's **internal numeric award id**
(`352043007`, the number in the `/award/<n>/` URL), not on a PIID. A PIID is the
identifier a researcher actually has in hand — off a contract, a press release, a
sibling worker's note — so passing one is the *expected* mistake, not an exotic
one. The endpoint 404s, and the 404 branch rendered that as a verified absence.

Severity: **highest possible for this tool.** A worker checking whether an award
exists would have concluded it does not. The `>= 400` branch already carried a
hint about constructed `CONT_AWD_...` strings 404ing, so the failure mode was
half-known — it just was not wired into the 404 path.

Fixed: a non-numeric id that 404s now returns `AccessBlocker` with a new
`WRONG_ID_TYPE` mechanism and a detail line that says NOT AN ABSENCE and names
the recovery path (look up by name, take `meta.record_id`). A numeric id that
404s is still a real, bounded `VerifiedAbsence`. Both paths have regression tests.

**Worth generalising:** any endpoint keyed on an internal surrogate id has this
shape. `record` and the courtlistener cursor paths should be audited for the same
"caller passed the human-facing identifier" case before 0.2.

**Worked well.** The `MATCHED 2 DISTINCT RECIPIENT NAMES` warning on `--sum`
caught a real error during the same tick: a worker reported a <vendor-f>
lifetime total of $938,792,019.07/34 and guessed the variance against
its own $915,073,700.81/33 figure was "a contract added between pulls." It was
not — the bare-name query spans `<VENDOR-F> LLC` (33 awards) *plus*
`<VENDOR-F> SUPPORT GROUP` (1 award, $23,718,318.26), and the two sum
to the larger figure exactly. The warning is what made that diagnosable in one
call instead of three. Keep it; consider making it louder in `--json`.

**Minor:** exit codes are correct (2 / 1 / 0 verified directly) — an earlier note
in this log suggesting otherwise was a shell artifact from piping into `head`,
which reports the pipe's status, not the CLI's. Not a tool defect.

---

## 2026-08-19 · CORRECTNESS AUDIT (not a usability pass) · claude-opus-5[1m]

Everything else in this file is agents reporting on their own runs. That is
good usability signal and it is **not** evidence the numbers were right: a
worker saying "`--sum` gave me a clean total" tells you the tool felt
trustworthy, not that it was. This entry closes that gap for seven figures the
tool produced today, several of which reached near-publication drafts.

**Method.** Each figure was recomputed against `api.usaspending.gov` directly
with a standalone script — separate paging, separate summing, no
cascade-search code in the path — and compared to what the tool reported. Per
the corpus rule: verify against the primary source, never against our own
writeups.

| # | Claim (as the tool reported it) | Independent recompute | Verdict |
|---|---|---|---|
| 1 | `W9124J24C0019` → <VENDOR-R>, $3,972,528 | identical | **CONFIRMED** |
| 2 | <vendor-f> LLC = $915,073,700.81 / 33 awards, `complete` | $915,073,700.81 / 33 | **CONFIRMED (to the cent)** |
| 3 | <vendor-a> = $3,298,091.75 / 52 awards | $3,298,091.75 / 52 | **CONFIRMED (to the cent)** |
| 4 | <vendor-d> = $1,564,745.51 / 15 awards, `complete` | $1,564,745.51 / 15, single recipient <VENDOR-D>, LLC | **CONFIRMED (to the cent)** |
| 5 | "<vendor-e-parent>" + "<vendor-e>" sums double-count | 283/283 "<vendor-e-parent>" awards are recipient <VENDOR-E> INC — 100% overlap | **CONFIRMED** |
| 6 | <vendor-d> PSC codes contradict its SaaS marketing | 5× `6910 TRAINING AIDS`, `U099`, `U008 TRAINING/CURRICULUM DEVELOPMENT`, NAICS `611519 OTHER TECHNICAL AND TRADE SCHOOLS` | **CONFIRMED, understated** |
| 7 | <vendor-g> FY-window `--sum` = $1,650,428,648.83 / 87 (post-fix) | identical | **CONFIRMED (to the cent)** |

**Seven for seven. No figure was wrong.** Four matched to the cent across
independent paging, which also means the paging and the `complete`-vs-floor
marker are doing real work rather than getting lucky on small result sets.

**Three findings worth keeping:**

1. **The $10.7B double-count is now proven, not merely suspected.** The worker
   inferred it from two shared PIIDs spotted by eye. The full comparison is
   starker: *every one* of the 283 awards returned by `"<vendor-e-parent>"` has
   recipient `<VENDOR-E> INC`, and the naive sum is $10,737,830,486.25 —
   the flagged figure, reproduced exactly. "<vendor-e-parent>" is not a second entity
   in this corpus at all. The eef4697 warning fires correctly on it.

2. **Claim 6 was understated by its own author, which is the safe direction.**
   The worker reported 4× `6910 TRAINING AIDS`; there are 5, plus a `U008
   TRAINING/CURRICULUM DEVELOPMENT` they did not mention and a NAICS
   `611519 OTHER TECHNICAL AND TRADE SCHOOLS`. The conclusion that the
   government classifies <vendor-d> as a training vendor — which corrected a
   standing research judgment — holds more strongly than the worker claimed.

3. **The one reported BUG was real, and its fix is verified independently.**
   The <vendor-g> `--from/--to` case (`--sum` returning an all-time total while
   self-reporting `complete`) was the single confident-wrong-answer defect in
   this log. Post-08fa7ae it returns $1,650,428,648.83 / 87 in-window, which
   the recompute reproduces to the cent, and which matches what `--count`
   said all along. The prior entry asked whether the fix generalised beyond
   the one vendor it was tested on; it does.

**Scope, stated honestly.** This audits USAspending figures only — the source
behind 305 of 552 logged calls and most of the dollar claims. It does NOT
audit `web`/`news` result quality, `extract` fidelity on PDFs, or CourtListener
counts, and a clean result here says nothing about those. It also cannot catch
an error shared by both paths: if USAspending's own data is wrong, both my
recompute and the tool inherit it. What it does establish is that
cascade-search is not introducing arithmetic, paging, or attribution errors of
its own on the source that carries most of its load-bearing numbers.

---

## 2026-09-19 · TRIAGE PASS + three false-absence fixes · claude-opus-5[1m]

Not a field report. A maintenance pass on this file and on what it had been
saying, unheeded, for a month.

### What the log was doing

After the 2026-08-19 build day, field reports kept arriving but almost none of
them turned into code: most commits in the following month appended to this
file, and no test was written in response to any report logged after the build
weekend. Every `[fixed]`/`[wontfix]`/`[tracked]` marker sat in the seed era.

The recurring themes were not new information. Dozens of entries re-reported
web-tier rate limiting and browser allow-list gaps -- unfixed root causes, each
re-derived at real cost. The clearest case: the CourtListener AWS WAF soft block
was reported **with a correct root cause and a written spec**, and reported
again weeks later by a worker who redid the whole diagnosis from scratch,
because nothing marked it known.

The thesis in CONTRIBUTING -- field reports beat synthetic tests -- is
*correct*, and the seed-era triage proves it. But a log nobody triages is an
archive, not an engine.

### Three false absences, all fixed, all with regression tests

Each is the tool manufacturing the exact failure it exists to prevent. None was
caught by the green suite, because all three built a *clean* `Coverage` over a
failure the source never noticed -- and the `__post_init__` guard can only catch
dirt that was **declared**.

1. **AWS WAF soft block read as a document read.** [fixed] `detect_blocker`
   ignored response headers entirely, so `x-amzn-waf-action: challenge` with a
   202 and an empty body flowed downstream as content and `extract` printed
   "the document was read, the terms are not in it." Now typed `AWS_WAF`,
   browser-escalatable, with the retry named in the detail. Detection keys on
   (challenge shape, no prose), never size alone, so a small real page stays a
   Hit.

2. **`usaspending.dollar_sum` certified absence over unparseable amounts.**
   [fixed] It counted only rows whose `Award Amount` was numeric, then reported
   "no priced rows" as `VerifiedAbsence` at **exit 11** -- which a shell caller
   reads as publishable. Real awards with string amounts ("5,000,000", a shape
   this module already defends against elsewhere) certified a vendor as having
   none. Now an `AccessBlocker`. The zero-rows-but-`hasNext` case is fixed too.
   A genuine absence now also carries probes and the subaward caveat instead of
   the bare string "Not found".

3. **Cached absences silently lost their scope.** [fixed] The cache stored only
   result rows, so a replay rebuilt the outcome without `probes`,
   `not_searched` or `caveats`: `claim()` degraded from a scoped sentence to
   "Not found" while still exiting 11. Scope now persists in a sidecar; old
   cache rows still read.

### The structural fix

`tests/test_every_source_absence.py` sweeps **every** source entry point against
transport failure, rate limiting, and a malformed success payload, asserting
that none yields a `VerifiedAbsence`. Adding a source means adding one line to
`ENTRY_POINTS`, and that line is what makes the guarantee cover you.

Two entry points are `xfail`, honestly: `oscn.search` and `docs.search` scrape
HTML, and a scraper counting rows cannot distinguish "the page served no
results" from "this is not the page I know." **Tracked, not fixed.**

### Allow-list: the recurring root cause

`ALLOWED_HOSTS` was a hardcoded set with no override, so every registry required
a commit and a release -- while the error message told workers to "add it to
ALLOWED_HOSTS deliberately" and gave them no way to. Now extendable via
`~/.evidence-search/allowed_hosts.json` (or `$EVIDENCE_ALLOWED_HOSTS`), **a
stated reason required per host**. The dot boundary still holds for
config-supplied hosts, a broken config fails closed to the baseline, and the
shipped list can never be reduced by a config file. `evidence-search hosts`
prints the current boundary.

### Still open

- **The `web`/SearXNG rate-limiting cluster.** Partly root-caused 2026-08-30
  (daemon on a deleted settings path; four default engines durably blocked).
  Not addressed here.
- **Read timeouts labelled `RateLimited`** (08-19 open item #3). Still true.
- **`N/M responsive` can mask a total outage** -- a high responsive count
  alongside zero results.
- **HTML scrapers cannot detect a malformed page** (the two xfails above).

---

## 2026-09-20 · federal criminal docket watch (multi-defendant case) · claude-opus-5

**Task:** answer a live question -- has the magistrate ruled on a defendant's
pending request, and are there docket entries newer than the last recorded
check.

**Commands:** `courtlistener '"<case phrase>"' --type r`; same `--type rd`; same
with `--json --limit 30`; `courtlistener --help`; `extract <docket-page-url>` with
three different `--grep` patterns and `--text`; same URL `?page=2`.

**Worked well -- and it caught a real error I had already committed.** Earlier
I had checked the same docket by probing
`storage.courtlistener.com/recap/<court>.<docket-id>/...N.0.pdf` for a range of
entry numbers, got 404s across the board, and recorded "all return HTTP 404 on
the RECAP mirror." `extract` against the **docket page** showed one of those
entries exists -- an order that materially changed the finding. Another entry
was a **"(Text-Only) ORDER"**, a class of entry that has no PDF at all and that
the bucket-probe method can *never* detect.

**Generalizable rule this suggests for SKILL.md** (the `mirror-not-a-register`
note currently stops one level short): *the docket page is the register; the PDF
bucket is the mirror.* A 404 at `storage.courtlistener.com/.../N.0.pdf` means no
PDF was ever fetched for that entry -- often because none exists -- and says
nothing about whether the entry does. Probing the bucket is not a docket check.

**Friction 1 -- `--type r` gives no `total_matches`; `--type rd` does. Severity:
slowed, near-miss on a count.** SKILL.md's counting guidance shows the
`results: 20 of 293 total_matches` header. On `--type r` that header is absent
(`results: 16`, nothing more) and `meta.total_matches` is not in the JSON. On
`--type rd` both appear. Same quoted phrase, different type. I nearly reported
"16" as a corpus total.

**Friction 2 -- the top-level `asked` block is missing on `courtlistener`.
Severity: annoyed.** SKILL.md says `--json` carries `asked` at the top level and
to check it before writing up an absence. Top-level keys were
`['query','coverage','results','outcome']`. On a `VerifiedAbsence` that
instruction would be unfollowable.

**Friction 3 -- `--type rd` results have empty `title` and blank metadata line.
Severity: slowed.** Every row rendered as a bare `*UNIQUE*` with an empty title
and a ` | | ` metadata line; snippets were empty in `--json`. The URLs carry the
doc number, so it was recoverable by regex, but the list is unreadable as
printed.

**Friction 4 -- `--version` is not supported. Severity: trivial.** It exits 2
with the usage block. A version would make these entries reproducible.

**Corroborating the open `--docket` request.** My first `--grep` pattern matched
160 times, because `ORDER` appears in every conditions-of-release entry in a
many-defendant case. The signal I needed was two lines of page metadata
(`Last Updated` / `Date of Last Known Filing`), which answer "is this view
current?" -- usually the real question on a docket watch.

**Also useful, unprompted:** the empty-result message on `extract` -- "This is an
absence IN THIS DOCUMENT ONLY" -- stopped me from over-reading a zero, and
`--grep` on the docket page hit 97.8-99.9% reduction on a ~184k-token page.

## 2026-10-01 · building `lda` and `fec` sources (#16, #19) · claude-sonnet-5

**Task:** implement the LDA and FEC sources #16/#19 scoped, following the
existing `run_source` pattern and the absence invariant.

**Real finding, not a synthetic one: FEC's `pas2` and `oth` bulk extracts are
NOT disjoint for committee-to-candidate rows.** `pas2` is documented as
"committee transactions to a candidate" and `oth` as "committee-to-committee";
the design note in `docs/extension-candidates.md` (and my own first draft)
assumed they partition cleanly. They do not: a PAC's contribution to a
candidate committee appears in BOTH files, byte-identical down to `SUB_ID`
(FEC's own globally unique schedule-line identifier) — verified against five
real contributions across three cycle files. Summing both files' rows for a
`committee X -> committee Y` query doubled every dollar figure (five $1,000
contributions read as $10,000) before `_dedupe_by_sub_id` was added. The
regression fixture now includes the real duplicate rows specifically so the
dedup path is exercised, not merely assumed — CONTRIBUTING's point about
`dollar_sum` shipping green without a test that would have caught it applies
here too; a fixture that only had the `pas2` side would never have failed.

**Performance note, from indexing real cycles.** A cold index of 3 cycles'
`pas2`+`oth` (~2M rows) took several minutes with per-row `INSERT` on the
default SQLite journal; switching to `executemany` + WAL + `synchronous=NORMAL`
(the same tradeoff `Store()` already makes for its own ledger) was the fix.
Noted here because "bulk-first" implies a tool a user will actually wait on —
worth probing if the real index later grows into the multi-cycle, multi-file
range the issue's "2006-2014" example implies; a 3-cycle index landed north of
250 MB on disk.

**LDA, by contrast, was clean.** `client_name`/`registrant_name` are confirmed
case-insensitive substring matches (not exact), which is in the caveat the
source returns, not a surprise found in the field.

Not re-reporting: the general sources surveyed in `docs/extension-candidates.md`
(#1-#20) — this entry is additive, about what building two of them turned up.
