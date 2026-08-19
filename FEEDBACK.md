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

## 2026-08-19 · eval-1-blocked-is-not-absence · claude-opus-5
**Command:** `cascade-search --wait oscn --county oklahoma --lname Frazier --year 2013`
**Expected:** docket rows for a common surname in the state's largest county, or a clean absence
**Got:** `AwaitingHuman`, exit 4, `coverage: 0/2 responsive | ERRORED: oscn, oscn:browser`, captcha gate token `9fe41fafed61`. Retried once, same wall, new token `a749a449f759`.
**Friction:** Minimal on the core job — this is the tool working exactly as advertised. The typed outcome plus the `0/2 responsive` coverage line made it immediately unambiguous that I had been blocked rather than found nothing. With a plain WebSearch I would have had an empty page and a real temptation to write it up as "no records found." That is the failure the tool exists to prevent, and it prevented it. Worth saying plainly: the single most useful thing here was that exit 4 is not exit 1.
**Had to figure out:**
  1. `oscn --help` does not say which county names are valid, or what format they take. I guessed `oklahoma` from the pattern in the SKILL examples (`caddo`). It worked, but I had no way to confirm in advance that `oklahoma` was the right token for Oklahoma County rather than, say, `oklahoma-county` or a FIPS code — and a wrong county string would presumably also produce a result I might misread. A `--county` value list, or an error that distinguishes "unknown county" from "county fine, blocked," would remove that doubt.
  2. Nothing in the blocked output tells me whether a gate for this exact URL is ALREADY queued. I had to run `gate list` separately to discover `ebace2555278` — a gate on the byte-identical Oklahoma/Frazier/2013 URL from a concurrent run. My two runs then added two more. Three gates for one query is noise for whoever clears them.
**Surprising:** Retrying created a SECOND gate token rather than returning the existing one. Gates look content-addressable in principle (same URL, same capture instruction), so dedupe — or at least "a gate for this URL is already queued as X" — seems like it would be strictly better than accumulating duplicates. `gate list` currently shows 8 gates of which at least 5 are same-URL duplicates (three Tulsa/Crotty-White/2015, two Oklahoma/Frazier/2013).
**Also surprising, smaller:** `$CS limits` showed oscn at `5/30 per 3600s` already consumed before I ran anything, which was genuinely useful context — I could see the concurrent run's footprint. But the oscn note says "Turnstile after ~10 fetches/session" and I hit Turnstile at what the ledger showed as fetch 6. Either the ~10 estimate is optimistic, or session-scoped and ledger-scoped counts are different things and the note does not say which.
**Would have helped:**
  - A one-line hint in the blocked output when an equivalent gate is already pending.
  - `--county` accepting/validating against a known list, or `oscn counties` to enumerate.
  - Clarifying in the oscn note whether the "~10 fetches/session" threshold relates to the shared-ledger count that `limits` reports.
**Where it saved real work:** The `coverage: 0/2 responsive` line did in one glance what would otherwise have been several minutes of manually poking the URL to work out whether OSCN was down, slow, empty, or walling me. And the exit-code contract meant I did not have to reason about whether my negative was trustworthy — the tool refused to let me construct one. For a "can I publish this negative?" question specifically, this is the right shape.
**Severity:** annoyed (gate duplication, county-name guessing) — the core outcome-typing worked and was the point.

## 2026-08-19 · eval-0-verified-absence-is-publishable · claude-opus-5
**Task:** does Bartholomew Quillfeather have a Caddo County OK court record from 2013

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
I had to guess that `--county caddo` is a lowercase bare name (not "Caddo County",
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

## 2026-08-19 · eval-2 general-web · claude-opus-5 (investigation-search skill)

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

**2. `extract --ids` silently over-filters and reports a misleading reduction number.**
**Command:** `cascade-search extract https://www.projectsaltbox.com/p/work-on-313-million-contract-to-convert --ids`
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

**3. No way to page a large result set without re-running the whole search.**
I wanted results 15-30 of a 30-result `web` hit. There's no `--offset`, no `--limit`, and the
output goes to stdout as formatted text, so I re-ran the identical query and piped through
`sed -n '55,120p'`. Second run was served fast (cache, presumably) but it still spends a
rate-limit slot on a query I already had.
**Would have helped:** `--limit/--offset`, or just say "use `--json | jq`" in the help text.
I know `--json` exists from the skill doc but it wasn't obvious it was the paging answer.
**Severity:** annoyed.

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
- `*UNIQUE*` markers were genuinely predictive on this beat. Project Salt Box — a small
  Substack doing the best USASpending.gov modification-record reporting on this story, better
  than the majors on procurement mechanics — was UNIQUE-flagged and would have been buried by
  consensus ranking. Two of my four headline sources came from UNIQUE hits.

**5. Small thing:** `--wait` worked as documented across sequential calls; zero spurious
`RateLimited`. Nothing to report there, which is itself the point.

## 2026-08-19 · foia-records-<person-h>-cbp-position-tenure-separation-date · agent:claude-sonnet-5-parallel-tick1-c
**Commands:** `cascade-search --wait web '"<person-h>" CBP FOIA'`, several follow-up `--wait web`/`--wait news` variants, `courtlistener "<person-h>" --type r`, `propublica "<person-h>"`, `extract https://www.cbp.gov/site-policy-notices/foia/records`
**Expected:** either a hit connecting a named person to a federal LE role, or a clean VerifiedAbsence/AccessBlocker split for a low-traffic, low-search-volume subject.
**Got:** mostly worked as documented. One recurring friction: intermittent `RateLimited` on `brave` even when `cascade-search limits` immediately after showed `brave usage: 0/20 per 1s` — i.e. the limits output didn't explain why the prior call was rate-limited. Simple retry after ~15s cleared it. Plausibly another concurrent worker (this is a multi-agent parallel-tick session) burning the shared brave/searxng budget between my `limits` check and the next call — if so this is expected/correct behavior, but the `limits` snapshot reads as "budget available" right after a `RateLimited` response, which is momentarily confusing for a solo user trying to self-diagnose vs. a shared-ledger race. A "last rate-limit event" timestamp or brief note in the RateLimited output ("shared ledger, another caller likely consumed budget between calls") would have made the diagnosis immediate instead of inferred.
**Worked well:** the `VerifiedAbsence` on `--wait news '"<person-h>" Border Patrol'` was exactly the right primitive for this task — a person-identity question where the deliverable is "confirm this genuinely isn't findable in the news index," and the typed outcome let me write that up as a clean negative with a one-line justification instead of hedging.
**Worked well:** `extract --grep`/plain `extract` on the CBP FOIA-process page was excellent for pulling the one operative fact (SecureRelease-only intake, effective date, routing table) out of a page that was otherwise mostly nav chrome — did not need to eyeball the raw page at all.
**Severity:** annoyed (rate-limit diagnosis friction only; no blocked work — retry resolved it in under 20s).

## 2026-08-19 · verify-what-our-rescue-is-actually-performing-under-legal-services-bridge-2-0-the-uscri-parallel-provider-question-and-subaward-records · agent:claude-sonnet-5-parallel-tick1-b
**Commands attempted:** `cascade-search docs "USCRI unaccompanied children legal services cooperative agreement" --site federalregister.gov`; also `cascade-search limits`.
**What happened:** the task needed federal-procurement-database verification (USAspending awards/subawards/transactions, Federal Register full-text search, FPDS-NG). None of these are `cascade-search` first-class sources — the closest fit, `docs`, is scoped to *vendor documentation* (`claude-code`/`claude-api` sites only per its own error message: "unknown docs site 'federalregister.gov'; known: claude-code, claude-api") and returned a `VerifiedAbsence` that was actually just "wrong tool for this corpus," not a real negative about federalregister.gov content. I nearly wrote that up as a publishable negative before re-reading the coverage line and realizing it meant "I don't know this site," not "this site has nothing."
**Worked around it:** dropped to raw `curl` against USAspending's public JSON API (`api.usaspending.gov/api/v2/awards/`, `/transactions/`, `/subawards/` via POST) and the Federal Register's own documented-ungated JSON API (`federalregister.gov/api/v1/documents.json`) directly — both worked fine unauthenticated, no rate limits hit, no CAPTCHA. Also hit a genuine SAM.gov/FPDS-NG session-gate (`406 Not Acceptable` from `sam.gov/api/prod/sgs/v1/search/` without an authenticated session) that I documented as an AccessBlocker by hand, since there's no cascade-search client for SAM.gov/FPDS-NG at all.
**Gap worth flagging:** this is the second-largest procurement/contracts beat in the corpus (federal contract awards, subawards, FPDS ceilings) and cascade-search has no dedicated USAspending or Federal Register client, only the generic `docs` command whose site allowlist is vendor-docs-only. A `cascade-search usaspending` (award/transaction/subaward lookups by PIID/FAIN/recipient) and a `cascade-search fedreg` (full-text search, the API is already confirmed ungated per a prior pass's method notes) would both be straightforward wins on this beat — same shape as the existing `oscn`/`courtlistener`/`propublica` clients, same typed-outcome benefit (right now a raw `curl` against USAspending gives no `Hit`/`VerifiedAbsence`/`AccessBlocker` distinction; I had to reason about "reporting lag vs. true absence" entirely by hand).
**Worked well once I dropped to curl:** USAspending's API is fast, unauthenticated, and well-structured — `spending_by_award` (POST, recipient_search_text + award_type_codes + time_period), `/awards/{id}/`, `/transactions/` (POST, award_id, sorted by action_date), `/subawards/` (POST, award_id) all returned clean JSON with no friction. If a client gets built, that's the shape to mirror.
**Severity:** annoyed, not blocked — the `docs --site` error message was actually good (named its own allowlist), which is what stopped me from mis-writing the VerifiedAbsence. But the absence of a procurement-data client on a research beat this procurement-heavy is a real coverage gap, not just a one-off.

## 2026-08-19 · industry-scale-count-the-use-of-force-training-expert-witness-market-via-courtlistener-... · agent:claude-sonnet-5-parallel-tick1-d

**Commands:** `cascade-search limits`; `cascade-search courtlistener "<vendor-b>" --type r`; `courtlistener '"<vendor-b-full>"' --type r`; `courtlistener "<vendor-c>" --type r`; `courtlistener '"<person-b>"' --type r`; `courtlistener '"<vendor-b-full>"' --type o`; `courtlistener "<vendor-c>" --type o`; `courtlistener "<person-c>" --type o`; `--wait` variants of most of the above; `extract https://www.ipicd.com/about.html --grep ...`; `extract https://www.forcescience.com/about/ --grep ...`; several `WebSearch` calls.

**1. The tool discards the one number this task most needed, and that's a real gap, not just my bad luck.** `cascade_search/sources/courtlistener.py`'s `_parse()` only reads `data.get("results", [])` from the CourtListener search API response — it never reads the top-level `count` field, which CourtListener's API does return and which is exactly "how many total matches" — the deliverable this task was scoped to produce. The CLI additionally caps display at whatever page size the API defaults to (20 rows). So every query that filled the page (`"<vendor-b-full>"` type=r, `"<vendor-c>"` type=r, both 20/20) had to be written up as "at least 20, true total unknown" rather than a real count — the single biggest quantitative gap in my final writeup exists because of this, not because the data isn't there. A one-line change (surface `data.get("count")` into the Coverage or a Result-level meta field) would close it. I did not attempt to page past row 20 or hit the API directly myself, since that would have meant either bypassing the shared rate-limiter (bad) or spending more of the shared daily budget chasing pagination by hand for a number the client should just report.

**2. `--wait` did not block through short (1-25s) rate-limit windows on courtlistener, contrary to what its own help text and the skill doc say ("blocks for short spacing waits instead of returning RateLimited (sub-minute only...)").** I hit the 5/min courtlistener window three times in a row; each time `--wait` returned `RateLimited` immediately (exit 3) with a `retry after: Ns` value under 30s, rather than sleeping and retrying. I had to manually re-issue the same command a turn later once the window had naturally ticked down. This is the opposite of a prior worker's note in this file ("`--wait` worked as documented across sequential calls; zero spurious RateLimited") — possibly the difference is per-source (that prior note was about `brave`/`web`, mine is specifically `courtlistener`), or possibly courtlistener's stricter concurrent 5/min-and-50/hr-and-125/day combination interacts badly with the wait logic (maybe it computes a wait against the wrong window, or refuses to wait when ANY of the three windows — not just the tightest sub-minute one — is exhausted). Worth a source-specific look.

**3. Query-construction discipline that worked, and its inverse that didn't, both worth encoding as guidance.** Exact-phrase institutional-name queries (`"<vendor-b-full>"`, `<vendor-c>` as a standalone acronym) returned clean, overwhelmingly on-topic result sets — I could eyeball 20/20 case names and immediately recognize the excessive-force/§1983 pattern. Personal-name queries (`"<person-b>"`, `"<person-c>"`) returned heavily diluted result sets — common surnames pull in unrelated bankruptcy, contract, and criminal cases with no connection to the subject, and CourtListener's full-text search gives no way to disambiguate without opening each docket. I discarded both personal-name query results rather than risk citing tokenizer noise as signal. This seems like a generally useful principle for `references/sources.md`: prefer institutional/proper-noun-phrase queries over personal-name queries on CourtListener whenever the person has a common surname, and treat 20/20-full personal-name results as inconclusive until spot-checked.

**4. Where it earned its keep.** The `type=o` (published opinions) vs `type=r` (RECAP dockets) distinction turned out to be the single most useful lever in this session — opinions are a much smaller, cleaner, and higher-confidence corpus (3 results for "<vendor-b-full>", well under the page cap, so very likely a near-complete count) than dockets (capped at 20, true total unknown). That distinction let me report two genuinely different numbers with different confidence levels instead of collapsing them into one misleading figure. `extract --grep` on ipicd.com and forcescience.com also worked cleanly and confirmed both domains **no longer 403-block automated fetch**, reversing a documented blocker from an earlier pass in this same research thread — worth a note that "previously blocked" should be periodically re-tested, not treated as permanent.

**Severity:** annoyed, not blocked. Worked around the count-discarding gap by explicitly labeling every number's tier/confidence in the writeup; worked around the `--wait` gap by manual retry.

## 2026-08-19 · reporting-leads-victim-voice-for-the-uac-legal-services-collapse · claude-sonnet-5-parallel-tick1-a
**Command:** `cascade-search extract /path/to/clsepa-v-hhs-complaint-2025-03-26.pdf --grep "Plaintiff" --grep "declares" ...` and later `--text`
**Expected:** matching passages (grep mode) or clean chrome-stripped text (--text mode) from a local court-filing PDF already archived in the KB.
**Got:** grep mode returned PDF *object-stream* noise — `/D [34 0 R /XYZ 84 588 0] /S /GoTo >> endobj 717 0 obj <<...` — instead of the document's actual paragraph text, even though the matched keyword ("Plaintiff") does appear literally in that noise (as part of PDF outline/bookmark titles) as well as in the real body text. `--text` mode was worse: it dumped raw PDF binary structure (xref tables, XMP metadata streams, font dictionaries) with a NEGATIVE reported reduction (-224.2%, i.e. the "extracted" output was over 3x the raw token estimate), never reaching the actual page-content operators.
**Friction:** could not tell from the tool's own output whether this was a "this PDF doesn't have an extractable text layer" case or a general extractor bug — there's no signal distinguishing "here is real matched content" from "here is PDF-syntax noise that happens to contain your grep string." I burned a few minutes reading extracted "text" before recognizing it was PDF structure, not body content. This specific document is a court complaint with a normal text layer (confirmed below) — not a scanned/OCR case where a failure would be expected.
**Had to figure out:** dropped to plain `pdftotext -layout` (Homebrew poppler, already on PATH) instead, which extracted cleanly (2,143 lines, real paragraph text, correct plaintiff-organization list with sworn caseload figures) on the same file. So the PDF is fine; cascade-search's extractor specifically mishandled it. I didn't dig into why (Aspose-produced PDF per its metadata — /Producer "Aspose.PDF for .NET 24.2.0 ... modified using iText Core" — possibly an object-stream/cross-reference-stream structure the extractor's parser doesn't walk correctly), just worked around it.
**Would have helped:** (1) a sanity check in the tool itself — if extracted text ratio vs. raw is negative or the output is mostly non-printable/PDF-syntax tokens, warn rather than silently return it as if it were prose; (2) grep mode specifically matching within already-extracted text rather than raw bytes, so PDF-structure false-positives on common words like "Plaintiff" (which legitimately appears in bookmark titles) don't leak through as if they were body-text hits.
**Where it saved real work elsewhere in this task:** `extract --grep` on live news articles (AZ Mirror, LA Times, NPR) worked exactly as advertised — 96-98% token reduction, clean quote-bearing passages, correctly attributed to speakers. The problem was specific to this locally-archived PDF, not the tool generally. `web` search coverage lines and the Hit/RateLimited typed-outcome distinction were both clear and useful — caught one genuine rate-limit (WaPo, on `extract`) that I correctly did NOT write up as a verified absence.
**Severity:** slowed (a few minutes; had a working fallback immediately available in `pdftotext`)

## 2026-08-19 · separate-the-two-vehicles-training-vs-expert-witness · claude-opus-4-8-parallel-tick1-e

Synthesis-heavy task (argue a structural claim from committed artifacts, not lookup). Reporting
specifically on whether the tool supports SYNTHESIS, since that was asked.

**Verdict up front:** `web` and `extract` were excellent and did real analytical work for me.
`courtlistener` was the weakest link and I ended up bypassing it with raw `curl` for the thing
that mattered most. The single most load-bearing evidence in this task — federal procurement
codes — has no cascade-search client at all, and I had to hand-roll USAspending API calls.

### Where it saved me real work (positive signal, be specific)

- **The browser tier is the standout feature and it is undersold in the skill doc.**
  `extract https://www.seakexperts.com/... --grep "<vendor-b>|<vendor-c>|..."` sailed straight
  through a Cloudflare wall that had just returned me a bare "Just a moment..." interstitial
  on `curl`. I did not ask for escalation, did not know it had happened, and got clean text.
  That silently turned a dead end into the census evidence for half my argument. **Suggestion:
  say in the output when the browser tier was used.** I only inferred it because I'd watched
  plain curl fail on the same URL 60 seconds earlier.
- `web` coverage lines let me trust a thin result set. `"Critical Appraisal of the Scientific
  Rigor" "<vendor-b>" Police Quarterly 2025` returned 18 results on
  `79/82 responsive | RATE-LIMITED: brave | ERRORED: duckduckgo, startpage` — I could tell that
  was a real hit on a near-complete sweep, not luck. That query closed a gap ("Seth Stoughton
  could not be located") that two prior passes had logged as unresolved.
- `*UNIQUE*` earned its keep. The Justia hit on the Tovar Daubert order was UNIQUE, and it was
  the thread that led to the single most important document in the whole task. Consensus
  ranking would have buried a district-court docket page.
- `extract --grep` on the Stoughton CV: 164K tokens of PDF down to a 60-line list of retention
  lines. That is genuinely a synthesis tool, not just a token-saver — the *shape* of the
  grep output (57 "Retained by plaintiff" vs 11 "Retained by defendant") WAS the finding.
  I did not have to read the CV to see the asymmetry; the extraction surfaced it.

### Where it fell short

**1. `extract --grep` works on PDFs; `extract --text` and bare `extract` do not. BLOCKED me briefly.**
Same URL, three behaviours:
```
$CS extract .../stoughton_seth.pdf --grep "<vendor-b>|expert|..."
  -> raw ~164,707 tok -> extracted ~3 tok (100.0% reduction)     # THREE TOKENS. Silent no-op.
$CS extract .../stoughton_seth.pdf --text
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
`*UNIQUE*`, topped by **Planetary Science Institute**, **Rey-Cruz v. Forensic Science
Institute**, and **Weizmann Institute of Science**. Zero relevant. The shell quotes are
consumed by the shell, so the API sees bare tokens. I had to figure out that
`'"<vendor-b>"'` (nested quotes) was the correct form. **A three-word query returning
Weizmann Institute should trip a "did you mean an exact phrase?" hint**, or the client should
phrase-quote multi-word queries by default.

**3. I abandoned the `courtlistener` client entirely for the task's key document.**
Once I had the docket, I wanted the actual order text. The client gives me search results,
not documents. I went to `curl https://www.courtlistener.com/api/rest/v4/search/?q=...&type=r`
directly, read `recap_documents[].filepath_local`, and pulled
`storage.courtlistener.com/recap/gov.uscourts.cand.376399/gov.uscourts.cand.376399.89.0.pdf`.
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
the `/doi/full/` variant. The browser tier did NOT pass it (unlike SEAK). Justia was also
`cloudflare-managed-challenge`. But `curl api.crossref.org/works/<doi>` returned the full
verbatim abstract, author list with affiliations (including `von.kliem@forcescience.com`,
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
calls and, more usefully, told me a sibling had already covered the USMS award stream — I only
found that out by reading a KB file afterwards.

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

## 2026-08-19 · FOIA-J&A task, Our Rescue/Burke Law Group contracts · agent:claude-sonnet-5-parallel-tick2-c
**Task:** determine whether an HHS FAR 6.302-2 urgency J&A is publicly available or genuinely FOIA-only, and draft the request if not. Explicitly asked to stress-test `fedreg` and `usaspending`, both hours old at the time.

**Worked well — `fedreg --type notice` gave a trustworthy negative.** `$CS --wait fedreg "Our Rescue" --type notice` returned 20 real results with a real corpus behind them, and none matched — which let me write "no FR notice exists for this J&A" as a claim I could stand behind, rather than "I didn't find one." That distinction is the entire point of the tool and it held up under an actual use case (deciding to file a FOIA request rather than keep hunting).

**Worked well — `usaspending "Our Rescue" --count` was exact and fast.** One call, one award, matched the KB's prior finding. No friction; this is the easy case (recipient-name match, not keyword).

**Friction 1 — `fedreg` phrase matching is not exact, and this matters for a J&A search specifically. Severity: annoyed, borderline slowed.** `fedreg "Our Rescue" --type notice` did NOT restrict to documents containing that phrase — it returned 20 unrelated agency-information-collection notices (NOAA, FAA, FCC Unified Agenda, arms sales) with no visible connection to "Our Rescue" in the title or snippet shown. I could not tell from the output alone whether this was a true zero (tokenized on "our" as a stopword-adjacent term, similar to the courtlistener unquoted-phrase problem documented elsewhere in this log) or a real ranked-but-irrelevant result set. I had to reason from FAR-authority knowledge (urgency J&As for *contracts*, unlike cooperative-agreement single-source-intent notices, don't require FR pre-publication) to conclude the emptiness was structurally expected rather than a tool miss — the tool itself gave me no signal either way. **Would have helped:** either (a) `fedreg` support a quoted-phrase mode with the same "quote it or your count is off by orders of magnitude" warning that `courtlistener` now carries, or (b) the output flag when zero of the returned rows contain the literal query string, so a worker doesn't have to eyeball 20 titles to notice none of them are about the subject at all.

**Friction 2 — no client for procurement-file attachments (GovTribe/HigherGov/SAM.gov contract-file documents). Severity: slowed, worked around.** The actual J&A, if posted anywhere short of FOIA, would live as a PDF attachment on a procurement aggregator or SAM.gov itself — `usaspending` and `fedreg` structurally cannot see this (neither indexes contract-file attachments). I fell back to `web`, which found the right aggregator listing pages via snippet text, but `extract` on GovTribe hit `AccessBlocker` (Cloudflare managed challenge) and `extract` on HigherGov returned 0 usable tokens (JS-rendered contract page, no matching text in the DOM). Neither is a `cascade-search` bug — both are genuinely hard targets — but it means "is there a J&A attachment on this specific award" is currently unanswerable by any typed-outcome path; I had to reason from the *absence of any hit* across three different search angles plus a same-office aggregator precedent (GovTribe does host J&As for *other* HHS/OMAS awards) to reach a defensible "genuinely FOIA-only" conclusion, rather than getting there from one tool call.

**Worked well — `AccessBlocker` on GovTribe was unambiguous and let me log a gap instead of a false negative.** Without the typed outcome I might have silently treated "extract returned nothing useful" the same for both the Cloudflare wall (GovTribe) and the JS-render miss (HigherGov) — but the explicit `AccessBlocker` on one and the visible 0-token/100% reduction on the other let me describe them differently in the work log (one is a real gap, the other is closer to a true empty match).

**Severity:** slowed overall — no wrong output produced, but reaching a defensible "FOIA-only" conclusion took four search angles plus outside FAR-process knowledge rather than one clean negative-finding call, because neither `fedreg` nor `usaspending` covers procurement-file *attachments*, which is where a J&A actually lives if it's public at all.

## 2026-08-19 · find-the-named-story (<vendor-b>/<person-c> human-story task) · agent:claude-sonnet-5-parallel-tick2-b
**Task:** find a named, tier-1 plaintiff/decedent story from the 155-quoted-phrase CourtListener docket set behind the <vendor-b>/<person-c> credential, following up on the prior worker's aggregate-scale RECAP census.

**Worked well — the quoted-phrase discipline from the skill doc was exactly right and I didn't have to rediscover it.** `courtlistener '"<decedent>" "<vendor-b>"' --type r` went straight to the one relevant docket on the first call because I quoted both phrases from the start; no tokenizer-noise detour this pass.

**Worked well — `meta.party` and `meta.recap_documents[].description` on a docket-search hit were exactly what closed this task.** The `--json courtlistener` hit for "Estate of <decedent> v. San Diego" returned `party: ["Hector Lopez", "Lydia Lopez", "Estate of <decedent>", "<decedent>, Jr.", ...]` and a `recap_documents` entry titled "TRIAL BRIEF Re: Defense Expert <person-i> and His Reliance on Unreliable Principles of <person-c> and <vendor-b-full>" in the same call. That single JSON response gave me the decedent's family names AND the exact document that ties the doctrine to the case, with no second query needed. This directly contradicts the "client drops recap_documents" friction logged earlier today by agent:claude-opus-5 on a different query shape — worth noting the field IS present on at least some `--json courtlistener` responses (mine used a two-phrase AND query, theirs used a single quoted phrase on `--type r` alone), so the drop may be query-shape- or code-path-dependent rather than universal. Flagging so maintainers can check whether it's actually two different call sites.

**Friction 1 — `extract` on Justia case-law pages hard-403s (Cloudflare), and there's no fallback path to the same case text. Severity: slowed.** `extract https://law.justia.com/cases/federal/district-courts/california/casdce/3:2013cv02240/424366/56/ --text` returned `AccessBlocker` (cloudflare-managed-challenge, HTTP 403) with no escalation success this pass (the skill doc says browser-passable blocks escalate automatically; this one apparently didn't clear, or the escalation happened invisibly and still failed). I worked around it by using `web` + secondary journalism (Courthouse News, San Diego Reader) instead of the court's own posted opinion text, which was a fine substitute here but wouldn't be for a case with no press coverage.

**Friction 2 — CourtListener RECAP document *text* is PACER-paywalled even when the document metadata/description is free via search. Severity: slowed, expected but worth naming.** The pivotal document in this task (ECF 172, the trial brief naming <person-i>/<person-c>/<vendor-b>) was fully identifiable by its `recap_documents[].description` field, but `extract`-ing the docket entry URL only returns the same metadata, not the brief's actual argument text — that requires a PACER purchase. Not a tool bug (RECAP genuinely doesn't have the document unless someone already bought and uploaded it), but worth flagging as a standing limit on this task type: a docket search can find *that* a challenge was filed and *who* filed it, but not *what it argued*, without a human with PACER access.

**Worked well — `web` coverage line caught a partial sweep I would otherwise have mistaken for a clean result.** Multiple `web` calls this session reported `RATE-LIMITED: searxng:brave` alongside 20-26 results; I read the coverage line each time rather than trusting the result count alone, per the skill doc's instruction, and it correctly kept me from treating any of these searches as exhaustive.

**Severity:** slowed (two access-blocker detours, both cleanly typed and logged, neither cost more than one extra query round each — the task closed the same session with a tier-1-sourced named story).

## 2026-08-19 · FOIA-routing task, CBP <vendor-a> courtroom-testimony SOW · agent:claude-sonnet-5-parallel-tick2-d
**Task:** determine whether CBP statements of work for four named <vendor-a>/<vendor-a> award IDs are FOIA-gated, and pull the SOW content if not.

**Worked well — `usaspending "<vendor-a>" --limit 30` found all four target awards by recipient name in one call**, and the `--json` output's `meta` block (award_id, amount, dates, description) was exactly what I needed to confirm/correct a task-file citation (one award, `70B03C26P00000169`, was listed unpriced in the task; the client's own data showed $28,800.00 immediately).

**Friction 1 — no way to look up a specific award ID directly. Severity: annoyed, worked around.** `cascade-search usaspending "70B03C26P00000169"` (the award ID itself, not a recipient name) returned a clean typed `VerifiedAbsence` — which is the *correct* typed outcome for "this string isn't a recipient name," but it cost me a call and a moment of doubt before I remembered award IDs aren't names and re-ran with the recipient name + a high `--limit` instead. A `usaspending --award-id <PIID>` mode (or documentation saying explicitly "this searches recipient name, not award ID") would have saved the detour.

**Friction 2 — `extract` on `usaspending.gov/award/{id}` pages returns effectively nothing. Severity: slowed, worked around.** `extract "https://www.usaspending.gov/award/360657558" --ids` reported "729 raw tok -> 4 tok extracted (99.5% reduction)" — that number *looks* like a success (a good reduction ratio) but the 4 tokens extracted were useless; the page is a client-rendered React SPA with no server-rendered payload for `extract`'s DOM/regex approach to find. I worked around it with a direct `curl` to `api.usaspending.gov/api/v2/awards/{id}/`, which is public, unauthenticated, and returned the full structured contract record (solicitation ID, competition type, offers received, funding/awarding office, place of performance) that neither the `usaspending` client nor `extract` surfaced. **Would have helped:** either have the `usaspending` client's award-detail path hit the v2 API directly (it clearly already talks to this API for search), or have `extract` flag "near-zero extraction on a URL with substantial page byte size" as a distinct signal rather than reporting a deceptively good-looking reduction percentage on a near-empty result.

**Friction 3 — no client at all for SAM.gov solicitation/opportunity lookups. Severity: slowed, worked around, but this was the actual finding.** The USAspending record for the on-point award named a `solicitation_identifier` (`70B03C26Q00000152`). That solicitation turned out to be a **public SAM.gov notice** whose full narrative description named the CBP station, unit, and course scope I was tasked with finding — i.e., SAM.gov solicitation text is sometimes a full substitute for a FOIA request, and there's no `cascade-search` path to it at all. I found and read it entirely via direct `curl` to `sam.gov/api/prod/sgs/v1/search/` (keyword/ID search) and `sam.gov/api/prod/opps/v2/opportunities/{id}` (full detail with the notice body) — both unauthenticated, both returned clean JSON, no captcha/Cloudflare encountered. This is the second time in this KB's recent history this exact unauthenticated-SAM.gov-API pattern has been the load-bearing access path (see the 2026-08-19 GSA-eLibrary/SAM.gov entity-snapshot document in cascade-research for the first). **This reads like a real gap, not a one-off**: a `cascade-search samgov` client (search by keyword/solicitation number, fetch opportunity detail/description) would directly answer "is this contract action public or FOIA-only" — exactly the question this task existed to resolve — in one typed call instead of three manually-reconstructed API calls.

**Friction 4 — the SOW attachment itself (the actual PDF) is behind a SAM.gov login even though the notice metadata is public. Severity: annoyed, correctly identified as a real boundary not a tool failure.** I could read the full solicitation narrative (which happened to contain most of what I needed) but not the numbered attachment ("Attachment 1 - Statement of Work") — SAM.gov's attachment-download endpoints 404'd on every unauthenticated path I tried. This is a genuine SAM.gov design boundary (free account required, not FOIA-gated) rather than a cascade-search gap, but worth noting for scoping any future `samgov` client: attachment download would need either an authenticated session or should be explicitly out of scope.

**Severity:** slowed overall (no wrong output; the substantive finding — one of four target SOWs is publicly readable without FOIA — came out of manual API reconstruction rather than a tool call, and would have been one clean call with a SAM.gov client).

## 2026-08-19 · re-pull FPDS/USAspending contract table for <vendor-a>, RECOVER-flagged fields · agent:claude-sonnet-5-parallel-tick2-a
**Task:** re-pull a garbled 52-award contract table (<vendor-a>/<vendor-a>) from live USAspending, recovering exact totals, by-sub-agency/by-year breakdowns, and full FPDS competition detail for 8 specific flagged awards, plus check for subcontracts/grants/name-variant recipients.

**Worked well — `usaspending "<vendor-a>" --limit 60` and `--count` matched exactly and fast.** One `--count` call confirmed 52 awards; one `--limit 60` (`--json`) call returned all 52 with clean `meta` fields (award_id, agency, sub_agency, amount, dates, description) — enough to reconstruct the entire by-sub-agency and by-year table without a second tool. This was the core of the task and it worked on the first try.

**Worked well — `VerifiedAbsence` on a recipient-name check gave me a real negative I could put in the KB.** `usaspending "<vendor-a> of NE LLC" --count` returned `VerifiedAbsence` in exit code 1, which let me write "this DBA has never itself been an award recipient" as a citable finding rather than "I didn't see it."

**Friction 1 — `--all-types` is broken for anything but `--count`. Severity: slowed, worked around.** `usaspending "<vendor-a>" --all-types --count` correctly reports `contracts: 52 | direct_payments: 3 | idvs: 2 | loans: 9`. But `usaspending "<vendor-a>" --all-types` (no `--count`, default or `--limit 70`) fails every time with `outcome: AccessBlocker`, `mechanism: http-5xx`, `detail: "HTTP 422: 'award_type_codes' must only contain types from one group."` — i.e. the flag mixes contract/loan/IDV/direct-payment type codes into one upstream request when listing, but must be issuing separate per-group requests when counting (since count works). This means `--all-types` can tell you a non-contract award *exists* but can never show you *which one* — you have to already suspect the gap and go around the tool to see it. I worked around it with a direct multi-group `curl` to `api.usaspending.gov/api/v2/search/spending_by_award/`, segmenting award_type_codes into contracts/loans/direct_payments/idvs myself. **Would have helped:** either fix the listing path to issue the same segmented per-group requests the count path apparently already does, or have the CLI's own help text say "`--all-types` supports `--count` only, not listing" so I don't find out via a raw HTTP 422.

**Friction 2 — the false-positive is dangerous, not just missing. Severity: slowed, this is the actual finding worth flagging.** Once I reconstructed the all-types listing myself, all 14 non-contract "hits" turned out to be *wrong-entity name collisions* — unrelated SBA COVID-era PPP/EIDL borrowers named "RELENTLESS LLC" (Raleigh NC and Gilbert AZ — different UEI, different state, nothing to do with the Montana training vendor), "BUILD RELENTLESS LLC," "TEAM RELENTLESS LLC," "TECHNICALLY RELENTLESS LLC," "BRELENTLESS LLC." The skill doc's guidance ("default matches recipient NAME — precise") describes the *contract* search correctly, but the loan/direct-payment/grant recipient-name matching is evidently much fuzzier than that promise — two of the four exact-string "RELENTLESS LLC" hits weren't even the same UEI. If I hadn't individually pulled each award's recipient-location record to check, I'd have reported "14 additional non-federal-training awards" as a real finding about this vendor, when the true number is zero. **Would have helped:** a caveat in the tool's docs (parallel to the existing keyword-vs-recipient-name warning) that recipient-name matching on the *assistance* award types (loans/grants/direct payments) is looser than on contracts, and/or surfacing `recipient_uei`/`recipient_hash` in the `meta` block so a worker can eyeball entity-identity without a follow-up fetch per hit.

**Friction 3 — `extract --text` silently truncates at ~4000 characters, mid-JSON, no truncation notice. Severity: slowed, real risk of silent data loss.** `extract "https://api.usaspending.gov/api/v2/awards/{id}/" --text` (the same page type the tick2-d entry above used as its workaround target) returned output that looked complete — a normal "raw ~N tok -> extracted ~M tok" reduction line — but every one of 7 award records I pulled this way cut off at exactly 4001-4002 characters, mid-field, invalidating the JSON. This happened silently: no warning, no "(truncated)" marker, nothing to distinguish it from a genuinely short document. I only caught it because I tried to `json.loads()` the output and got a parse error on all seven files at almost the identical byte offset. Anyone trusting the extracted text directly (e.g. reading it as prose rather than parsing it) would never notice a mid-record cutoff. I worked around it with direct `curl` (same as tick2-d's independent workaround for the plain award page — this is the *raw API endpoint* version of that same detour, and it also has its own tool-side problem). **Would have helped:** either raise `--text`'s cap for machine-readable content types (JSON in particular, where truncation actively corrupts rather than just shortens), or explicitly flag truncation in the output the way the OCR path already flags itself ("source: OCR ... NOT verbatim").

**Corroborating tick2-d's finding from earlier today:** `extract` on `usaspending.gov/award/{id}` (the React SPA page, not the API) also returned near-zero useful content for me (`raw ~729 tok -> extracted ~4 tok`), same as their report. Direct `curl` to `api.usaspending.gov/api/v2/awards/{id}/` was the right workaround both times — worth promoting to a documented pattern (or building into the `usaspending`/`record` client directly) since two independent workers hit the identical wall and found the identical fix within the same day.

**Severity:** slowed overall (no wrong output shipped — every figure in the final KB write-up was individually verified against a working access path — but three separate detours: all-types HTTP 422, a false-positive that took real cross-checking to defuse, and a silent truncation bug that could have corrupted every one of the 8 flagged-award records if I hadn't tried to parse them as JSON).

## 2026-08-19 · <county-a> ESAC vendor-attribution FOIA (synthesis-heavy task) · agent:claude-opus-4-8-parallel-tick2-e
**Task:** determine who <county-a> actually paid with federal equitable-sharing "Training and Education" money — a question the ESAC/ISP forms structurally cannot answer, so the job was finding *which corpus could*, not running a lookup. I was asked specifically to report on whether the tool supports **synthesis** rather than just lookup.

**On synthesis specifically — the honest answer is that it supported the two ends and not the middle, and the middle is where the finding came from.** The tool was excellent at *bounding* (proving the federal corpus can't answer this) and at *verifying* (checking my claims against FAR and ILCS text). But the actual discovery — that <county-a> publishes a payee-level Claims Paid Report nobody in three prior passes had found — came from a `web` search whose top-10 I read for *structure* rather than for an answer: result #2 was a PDF agenda packet whose snippet mentioned "3,118 payments." Nothing in the tool surfaced that as significant; it ranked 2nd because of keyword overlap, not because it was the corpus-shaped answer. That's fine — but it means the synthesis step was entirely mine, and the tool's contribution was not burying the lead. Worth knowing that's the current ceiling.

**Worked well — `usaspending --count` + `--limit` turned "we couldn't find it" into a structural argument.** 52 awards / $3,298,091.75, zero mentioning Illinois or any county. The value wasn't the absence; it was that a *countable* corpus let me say **why** the absence is meaningless — a county spending federal equitable-sharing money isn't a federal contracting party, so USAspending couldn't show this payment even if it happened. A fuzzy source could not have supported that sentence. This is the single best thing the tool did for me.

**Worked well — `extract --grep` against FAR Part 5 and 5 ILCS 140 as a fact-checker.** 140,170 → 3,272 tok and 166,469 → 1,423 tok, and in one case it **corrected me**: I had started to write that the ~$24,500 award clustering sat under the simplified acquisition threshold. It doesn't — the SAT was $150,000 in 2010-11; $25,000 is the FAR 5.101(a)(1) *public-synopsis* threshold. Pulling the regulation text cheaply enough that verifying was reflexive rather than a chore is what caught it. Same for 5 ILCS 140 (5 business days / 50 free pages / $0.15 cap) — all three went into a FOIA draft as verified rather than remembered.

**Friction 1 — `extract` on a large REMOTE PDF silently returns nothing while reporting "100.0% reduction". Severity: slowed; would have been *blocked* if I'd trusted it. This is the one I'd fix.**
Same file, same pattern, two paths:
```
$ cascade-search extract "https://www.kanecountyil.gov/Lists/Events/Attachments/5069/AG%20PKT%20-%2019-05%20COB.pdf" --grep "VENDOR"
== Extract == https://www.kanecountyil.gov/...
raw ~3,630,887 tok -> extracted ~3 tok (100.0% reduction)
EXIT=0
```
```
$ curl -sL -o kane2019.pdf "https://www.kanecountyil.gov/Lists/Events/Attachments/5069/AG%20PKT%20-%2019-05%20COB.pdf"
$ cascade-search extract kane2019.pdf --grep "VENDOR"
== Extract == kane2019.pdf
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

**Friction 3 — no `--json` on `extract`, so structured docs have to be re-parsed by hand. Severity: annoyed, worked around.** These claims reports are genuine tables (VENDOR | NATURE OF CLAIM | DEPT | FUND | AMOUNT | DATE). `--tables` exists and is the obvious fit, but on a 400-page packet where table rows are interleaved with narrative I couldn't get it to give me rows I could trust, so I fell back to `pdftotext -layout` + my own parser. **Would have helped:** `extract --tables --json` emitting row arrays, even best-effort with a confidence flag. Not a blocker — but "the corpus is tabular and the tool reads it as prose" is the recurring shape of this beat's documents (ESAC extracts, ISP XLSX, county claims registers).

**A note on `web` that is a compliment and a caveat.** `web "<county-a> Illinois vendor payments checkbook transparency accounts payable disbursements"` is what cracked this task — the county's own agenda-packet PDFs surfaced at #2 and #4, and the `*UNIQUE*` markers were right that these were single-engine finds. But the top hit for both of my Kane-County-payment queries was the *Illinois State Comptroller*, which is the wrong corpus by construction (it holds State of Illinois payments; <county-a> is a unit of local government and its disbursements never pass through it). Ranking put the authoritative-looking-but-structurally-irrelevant source first and the actual answer fourth-ish. No fix implied — just: on government-finance questions the top hit is often the biggest agency rather than the right jurisdiction, and reading down mattered here.

**Severity:** slowed overall. Nothing wrong shipped, and one wrong thing (the SAT/synopsis-threshold mixup) was caught *by* the tool. But Friction 1 is a live correctness hazard for exactly the "defensible negative" use case the tool exists for — a silent empty read on a large remote PDF is indistinguishable from a clean extraction.

## 2026-08-19 · <vendor-a> parent-task synthesis pass (no field research) · agent:claude-sonnet-5-parallel-tick3-c
**Task:** claim the <vendor-a>/<vendor-a> PARENT task, read every child task's work log + the org profile, and update the parent's own deliverable with a cross-cluster synthesis and a highest-value-gap recommendation. Explicitly not a cascade-search research task — I never called `cascade-search` myself this pass, so I have nothing to add to today's rich thread above about `--all-types`, `extract` truncation, or `web` ranking. This entry is about `~/kb/kb` (Pyrite) instead, which is in scope per the skill's own framing ("report friction on a tool you just used").

**Friction — the state machine rejects a `claimed → done` transition with no shortcut, even when the work is genuinely already done. Severity: annoyed, cheap workaround.**
```
$ ~/kb/kb task update "mark-action-run-montana-sos-interactive-search-..." -k cascade-research -s done
ERROR [VALIDATION_FAILED]: Cannot move task from 'claimed' to 'done'. Allowed
next: in_progress, cancelled. Tasks follow open → claimed → in_progress →
done/failed/blocked/review; walk through the intermediate states rather than
skipping (or use 'cancelled' to retire an obsolete task from any state).
```
This was a "MARK ACTION" ticket: assignee had already been hand-set to `mark-completed-human-session` by whoever filed the follow-up notes, and the actual work (a Montana SOS interactive session) was visibly done — both target files (`organizations/<vendor-a>-profile.md`, `actors/<person-h>.md`) already carried the resolved finding. The task file itself was just never advanced past `claimed`. The error message is actually good — it names the exact allowed-next set and the intended path — so I could self-serve immediately (`-s in_progress` then `-s done`, two calls). Not a real blocker. But it's worth noting the specific shape: a *human*-attributed completion (assignee is a human-session marker, not an agent) still has to walk the same agent-oriented state ladder as a machine-claimed task, which reads a little oddly — there's no "record a completed human action" shortcut distinct from "advance a claimed-but-unstarted-by-anyone task."
**Would have helped:** nothing urgent — the error message already told me the fix. Maybe worth a one-line callout in the skill's Step 11 ("Transitions") section noting that `claimed`/`review`/`blocked` states sometimes need an explicit intermediate `-s in_progress` hop even when you're just closing out someone else's already-finished work, since a first-time reader might reasonably try `claimed → done` directly (I did) and only learn the ladder from the error.

**Worked well — `task list -f json` piped through a small python filter was the fastest way to get a clean cross-child status table.** For a synthesis task specifically (as opposed to a single-task research pass), what I actually needed first was "what state is every sibling task in, right now" — a single `task list -k cascade-research -f json | python3 -c "..."` grep-and-print got me a full status/priority/assignee table for the ~15 sibling tickets in one call, which is what let me tell (before reading a single work log) which threads were live-claimed by other agents this tick (stay off) vs. genuinely stale/open (worth flagging as the next dispatch). This isn't a novel pattern relative to the skill doc, but it's worth confirming it scales fine to a ~15-sibling family with no friction.

**Observation, not a complaint — the org profile file itself is where most of the real synthesis already lived.** By the time I read `organizations/<vendor-a>-profile.md` in full, nearly everything I would have wanted to say in a parent-task synthesis was already stated there, per-section, by the workers who wrote it (each section timestamped and self-correcting against the master memo). The parent task's own "Work Log" ended up being a pointer-and-triage document — what's closed, what's live, what's genuinely untouched — rather than new analysis, because the analysis had already happened in the org profile. Not a tool problem, just worth naming for whoever designs the next parent-task synthesis prompt: if the org profile is well-maintained, the parent task's marginal value is triage (which sibling to dispatch next), not restating findings.

## 2026-08-19 · S1 keystone rewrite — verifying Du Bois 1900 Paris Exposition facts · agent:claude-sonnet-5-parallel-tick3-d
**Task:** a draft-revision ticket (rewrite THE WITNESSES keystone), so most of the work was reading corpus, not searching. The one place I genuinely needed the open web was verifying new claims about Du Bois's 1900 Paris Exposition data portraits — this material did not exist anywhere in the KB and I was about to make it the piece's cold-open artifact, so it needed independent verification before I'd trust it in a keystone.

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

**Net:** clean, fast, no wasted calls. Two `web`/`extract` calls got me from zero corpus coverage to three independently-corroborated facts (collaborators, chart count, reception/Grand Prize) I was comfortable citing in a keystone piece. This is a small, low-drama use case relative to today's other entries, but it's the kind of "does this actually save time on a real verification need" test the tool should keep passing.

## 2026-08-19 · Burke Law Group $150M ORR withdrawal (task orr-withdrew-the-burke-law-group-...) · agent:claude-opus-4-8-parallel-tick3-a
**Task:** find why a $150M ORR single-source cooperative agreement to a Houston law firm was withdrawn 11 days after it was announced, what replaced it, and who the firm is. `fedreg` + `usaspending` + `courtlistener` + `extract`, plus WebSearch.

**Worked well 1 — `fedreg` answered the central question outright, and its two-result precision was itself the finding.** `cascade-search --wait fedreg "Burke Law Group"` returned exactly the two notices as results #1 and #2 (both `*UNIQUE*`), and — this is the part that mattered — nothing else Burke-related in the whole corpus. "There is no re-announcement of the $150M" is a claim I could only make because the corpus is defined and the result set was clean. Results #3-20 were obvious noise (Medicare OPPS, gas pipelines, marine mammals) matching on "Burke"/"Law"/"Group" separately, which is fine and self-evidently ignorable. This is the tool doing exactly what it's for.

**Worked well 2 — `extract --text` on a paywall-adjacent news page, 95.5% reduction, and it beat WebFetch outright.** WebFetch on houstonchronicle.com returned "unable to fetch" and on texastribune.org returned a hard `HTTP 403 Forbidden`. `$CS --wait extract "https://www.houstonpublicmedia.org/.../559368/..." --text` pulled the full AP wire story at ~31,788 → ~1,419 tokens, and that single extraction carried the ORR press statement, the firm's "small portion" quote, the Shapiro/Shubow roster detail, the USCRI $20M/through-December replacement figure, AND the CLSEPA plaintiff quote. Four of my five open questions came out of one call. `extract` on a syndicated-wire mirror is a better move than WebFetch on the paywalled original, and that's now twice in this log.

**Worked well 3 — `usaspending --count --all-types` gave me the publishable negative the writeup needed.** `VerifiedAbsence` on "Burke Law Group" is load-bearing in the artifact ("no federal money of any type ever reached this firm"), and I would not have written it that confidently off an empty list.

**Friction 1 — I re-committed the documented `head` exit-code mistake, on the same day it's warned about two entries above mine in this file. Severity: annoyed, self-inflicted, but suggests a fix.**
```
$ $CS --wait usaspending "Burke Law Group" --count --all-types 2>&1 | head -25
echo "=== EXIT: $? ==="     # printed 0
```
Then, unpiped:
```
$ OUT=$($CS --wait usaspending "Burke Law Group" --count --all-types 2>&1); echo "TRUE EXIT: $?"
TRUE EXIT: 1
```
**Observation:** exit was 0 through the pipe, 1 without. **Conclusion:** the tool is correct and I was wrong — same as the two agents cited in SKILL.md. What's interesting is that I had *read that exact warning* in SKILL.md maybe fifteen minutes earlier and still did it, because piping to `head` is muscle memory for "don't blow up my context," and the outcome word `VerifiedAbsence` was printed right there in the body so I wasn't reading for the exit code at all. **Would have helped:** the human-readable output already prints the outcome name — that's what saved me. So the fix may be docs-side and small: SKILL.md teaches `if $CS ...; then` exit-code branching in its very first example, *then* warns about pipelines 150 lines later under "Report the friction." Putting the pipeline caveat immediately adjacent to the exit-code branching example (where the reader forms the habit) would land better than putting it in the feedback section (where the reader is already done). Cheap doc reorder; this is now the third independent report of the same stumble.

**Friction 2 — `web` returned zero results with 78/82 coverage and honestly refused to certify, which is correct behavior, but `retry after: Nones` is a broken string. Severity: cosmetic (the `Nones`), annoyed (the empty result).**
```
$ $CS --wait web "\"Burke Law Group\" Houston immigration ORR \$150 million unaccompanied children" 2>&1 | head -40
== RateLimited ==
coverage: 78/82 responsive | 1 distinct index(es) | RATE-LIMITED: searxng:brave, searxng:google cse | ERRORED: searxng:duckduckgo, searxng:startpage
retry after: Nones
detail:    Cannot certify absence: coverage incomplete ...
```
`retry after: Nones` is a `None` interpolated into an f-string with an `s` suffix — should suppress the line entirely when there's no retry-after value, or print `unknown`. Tiny, but it's the kind of thing that makes an agent wonder if it mis-parsed the output.
The substantive half: this was a *heavily* loaded query (quoted phrase + five extra terms) and the story was on the front page of NPR/ABC/Texas Tribune that week, so zero results with 78/82 engines responding is a surprising miss. I switched straight to WebSearch, which returned ten on-point outlets on the first identical-intent query. **Not filed as a bug** — SKILL.md is explicit that `web` is a discovery tier whose engines don't honour quoted phrases, and that a `web` zero is a weak negative; the tool told me so and downgraded correctly. But the practical lesson for the docs is narrower than "web is fuzzy": **`web` degrades badly on long queries with a quoted phrase, precisely the query shape an agent writes when it wants a specific entity.** One line — "keep `web` queries short; move entity precision to a corpus source" — would have saved me the call.

**Where the results fell short (corpus gap, not a bug) — `courtlistener '"Burke Law Group"' --type r` was the wrong corpus for the question, and its `*UNIQUE*` hits are false friends.** 20 results, `total_matches` present, all fine mechanically. But the firm was founded in 2023 and had no federal award, so the phrase matches mostly as *counsel of record on unrelated dockets* (Smith v. Ideal Towing, Texas v. EPA, an opiate MDL) — a fuzzy-name-collision problem that the recipient-name-vs-keyword caveat in SKILL.md covers for `usaspending` but nothing covers for `courtlistener`. Result #5 *was* CLSEPA v. HHS, which looked like a hit and was really the phrase appearing in a docket I'd already read from another route. **Would have helped:** a caveat parallel to the usaspending one — "a quoted firm name in `courtlistener` matches attorney-of-record appearances, not party status; use `--type d` or read the party field before treating a hit as involvement." I nearly recorded "Burke Law Group appears in the CLSEPA docket" as a finding before checking what the match actually was.

**Note on the task rather than the tool, since it affects dispatch:** the ticket said "the corpus has almost nothing on it," but a prior pass had already archived both FR notices in-repo with SHA-256 and written a source note containing the withdrawal's operative sentence. My first `fedreg` call was therefore redundant with work already on disk. A `~/kb/kb search` before the first cascade-search call is what caught it — worth keeping that ordering in the worker skill (it already is, Step 5 item 1; I'm confirming it earns its place).

**Severity:** slowed at worst. All four research questions were answered, three from tier-1 primary documents, and the one confident-wrong-answer risk in the task (asserting the injunction caused the withdrawal) was avoidable because the *primary document itself* named the actual cause. `fedreg` + `extract` did the real work here.

## 2026-08-19 · <county-a> Claims Paid multi-month sample (sample-<county-a>-claims-paid-reports-...) · agent:claude-sonnet-5-parallel-tick3-b

**[fixed — see 2026-08-19 · foia-follow-through-vendor-attribution-for-<vendor-a>-training-dollars-esac-isp-appropriation-half-is-documented entry below, re-verified against the same file/command shape the same day]**
**Task:** sample 18 <county-a> Board agenda-packet PDFs (Finance and Budget Committee, 2016-2023) for
Sheriff training-category payee rows, checking for a handful of specific vendor names ("<vendor-a>" etc).
The task prompt explicitly warned that `extract` had previously grepped raw PDF binary on these large
county packets and reported a false "100.0% reduction, zero matches" absence, and said this was "fixed
within the last hour" — verify the `source: PDF text layer (N chars, exact)` line is present before
trusting a result. So this session both used the tool and specifically re-tested the claimed fix.

**The remote-PDF text-layer fix is real and verified — worked well.** On the actual packet PDF I was
working with (`AG PKT 23-10 Finance.pdf`, 1.57M chars, ~394K raw tokens):
```
$ cascade-search extract "https://www.kanecountyil.gov/Lists/Events/Attachments/6723/AG%20PKT%2023-10%20Finance.pdf" --grep "Sheriff" --wait
== Extract == https://www.kanecountyil.gov/Lists/Events/Attachments/6723/AG%20PKT%2023-10%20Finance.pdf
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
$ cascade-search extract "https://www.kanecountyil.gov/Lists/Events/Attachments/6723/AG%20PKT%2023-10%20Finance.pdf" --grep "Desert" --wait
== Extract == https://www.kanecountyil.gov/Lists/Events/Attachments/6723/AG%20PKT%2023-10%20Finance.pdf
source:   PDF text layer (1,578,283 chars, exact)
raw ~394,570 tok -> extracted ~28 tok (100.0% reduction)
$ echo $?
0
```
No match lines, no "no matches found" message, exit 0 — the *exact same output shape* the task warned
about, except this time the `source:` line is genuinely accurate (the fix for the binary-grep bug is
real), so the failure mode has moved rather than disappeared. I confirmed "Desert" (capital D) appears
twice in the document via `grep -c -i desert` on my own `pdftotext -layout` output of the identical PDF —
ground truth is a real hit, not a typo on my part. To rule out case-sensitivity or a stopword issue I
retried with `"<vendor-a>"`, `"interdiction"`, `"LEADSONLINE"`, and `"CelleBrite"` — all four are
verified-present strings in the same p-card table (LeadsOnline LLC and CelleBrite Inc. are payee names a
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
tasks in this KB will hit: `kanecountyil.gov` runs on SharePoint, and its committee page
(`/Pages/CountyBoard/committee.aspx?cID=7`) embeds the exact `_api/web/lists/getbytitle('Events')/items?
$select=...&$expand=AttachmentFiles&$filter=Status eq 'Scheduled' and Committee eq <id>` query it uses
client-side to render the meeting list. Calling that endpoint directly with `curl` + `ACCEPT:
application/json;odata=nometadata` returned all 334 Finance Committee meetings back to 1999 with their
packet-attachment filenames and URLs in one shot — far better than guessing packet URLs via `web`/
WebSearch one month at a time, and something `cascade-search` has no dedicated client for (reasonably —
this is one CMS's private-but-undocumented API, not a general public-records source). Flagging in case a
"government SharePoint sites often expose their event/document list via `_api/web/lists`" pattern note
belongs somewhere findable for future county-government tasks, since it isn't specific to <county-a>.

## 2026-08-19 · foia-follow-through-vendor-attribution-for-<vendor-a>-training-dollars-esac-isp-appropriation-half-is-documented · claude-sonnet-5

**Context:** re-checked the same large <county-a> PDF (`<county-a>-board-agenda-packet-2023-10-claims-paid-september-2023.pdf`,
1,578,283 chars) that an earlier pass on this investigation flagged for the now-supposedly-fixed
`extract --grep` global-match-cap bug (see prior entry above, same file). The task prompt told me the fix
had landed within the hour and asked me to re-verify if I hit that behavior again.

**Command:**
```
$CS extract "$F" --grep "Fifth Third" --grep "Procurement Card" --grep "Purchase Card"
```
**Got:** `'Fifth Third' matched 223 times, showing first 40; 'Procurement Card' matched 98 times, showing
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
card charges under the card issuer ("Fifth Third Bank") with no per-transaction line, and there is no
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

## 2026-08-19 · brief-write-detention-executive-branch-artifact task · claude-sonnet-5

**Command:** `$CS fedreg "detention bed mandate" --from 2003-01-01 --to 2005-12-31 --wait`
**Expected:** Either a Hit pointing at something plausible, or a clear signal that the Federal Register isn't where this document type lives.
**Got:** `Hit`, exit 0, 11 results — all completely unrelated (endangered-species critical-habitat rules, hours-of-service trucking rules, ADA accessibility guidelines). None mentioned detention or immigration. The tool returned a confident-looking `Hit` for a query that had zero real matches in the corpus it searched.

**Friction:** The task called for dating a "congressional detention bed mandate." That mandate is an *appropriations-bill line item* (a rider in a DHS Appropriations Act), not a Federal Register rule/notice — Federal Register covers agency rulemaking, not appropriations riders passed by Congress. `fedreg` doesn't know this and doesn't say so; it just relevance-ranks whatever's in its index against the query terms and returns the top N with a `Hit` verdict, even when every result is noise. Per the skill doc's own warning ("relevance-ranked though, so a row count is not a measurement") I didn't take the row count as a finding — but a first-time user without that warning fresh in mind could easily skim 11 titles, see nothing obviously wrong, and half-trust it.

**Worked around it:** switched to `$CS web "..."` (general web/SearXNG tier) which correctly surfaced congress.gov, govinfo.gov, and multiple advocacy-org citations pointing to the actual two statutes involved (P.L. 108-458 IRTPA §5204, and P.L. 111-83 DHS Approps Act 2010) — then went directly to govinfo.gov via `$CS extract` and pulled the codified statutory text to confirm both dates and language. That combination (web search to find the right primary-source URL, then extract to pull the exact clause) is what actually answered the question; `fedreg` contributed nothing to the finding despite returning a `Hit`.

**Would have helped:** `fedreg` distinguishing "no relevant hit above some relevance floor" from "here are the top N regardless of relevance" — even a low-confidence flag on the result set (e.g., "top result score below typical-hit threshold") would have saved the detour of reading through 11 titles to confirm none were on-topic. Not asking for corpus expansion — appropriations riders genuinely aren't Federal Register documents, so this is a "wrong tool for this document type" case, not a coverage gap. A one-line hint in the tool's own output ("no Federal Register document is expected to contain congressional appropriations language — try `web` or a congress.gov lookup for statutes/riders") would turn a silent near-miss into a fast redirect.

**Worked well:** `$CS extract <govinfo.gov URL> --grep "5204" --grep "detention" --grep "8,000"` on the full codified IRTPA text (raw ~205,789 tok) returned exactly the clause needed at ~1,432 tok — a 99.3% reduction — and it was the *exact* statutory language ("shall increase by not less than 8,000... subject to the availability of appropriated funds") that let me catch and correct an imprecise date claim in the task ticket itself (ticket said "2004 congressional bed mandate"; the real enforceable floor is P.L. 111-83, enacted 2009-10-28). `web`'s coverage-honesty line (`80/82 responsive | RATE-LIMITED: brave | ERRORED: startpage`) also did exactly what the skill doc promises — I could see it wasn't silently missing engines.

**Severity:** annoyed (fedreg) / worked-well (web + extract combo)

## 2026-08-19 · fact-check-du-bois-paris-exposition task · claude-sonnet-5

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

## 2026-08-19 · map-the-wider-after-academy-training-vendor-market (<vendor-a> cluster) · claude-opus-4-8-parallel-tick4-b

Vendor-market census: was <vendor-a>/<vendor-a> an outlier or an instance? Ran ~25 `usaspending`
count/group queries, ~6 `courtlistener`, ~5 `web`, 2 `extract`. **The tool answered the ticket's
structural question, and five prior passes using consumer web search had failed on it.** Detail below,
but the headline for maintainers: `usaspending` recipient-name search is the thing that broke a
question that had been stuck in this KB for eight days.

**Friction 1 — `courtlistener` has no `--format json`, and the failure is a Python traceback.**

**Command (exact):**
```
$CS courtlistener '"Calibre Press"' --type r --wait --format json 2>/dev/null | python3 -c "...json.load(sys.stdin)..."
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
**Severity:** slowed. It also degraded a finding: I could not distinguish whether `"Calibre Press"`'s 20
rows were the whole match set or a page, so I wrote it up as unverified rather than as reach evidence.

**Friction 2 — `extract --grep` returning "~3 tok / 100.0% reduction" reads as success but means zero matches.**

**Command (exact):**
```
$CS extract "https://www.courtlistener.com/docket/65524587/margarito-t-lopez-v-city-of-los-angeles/" --grep "Calibre" --grep "Glennon" --grep "Street Survival"
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

**Friction 3 — `usaspending --group contracts --limit 30` caps output below the vendor's award count, with no total.**

**Command (exact):**
```
$CS usaspending "Killology" --group contracts --limit 30 --wait
```
**Got:** 30 rows for a vendor that `--count` reports as **45 awards**. No aggregate obligation figure
anywhere in either mode — `--count` gives award *counts* only, `--group contracts` gives per-award
dollars. To get "what has this vendor been paid," I had to scrape the printed rows with a regex and sum
them in Python, once per vendor, ~10 times.
**Friction:** every dollar total in my deliverable is therefore a **floor**, not a total, for any vendor
with >30 awards (Killology, Blue Courage, VirTra 253, Oak Grove 220). I had to caveat the census table
accordingly, which materially weakens it — "at least $217,454" is a much weaker claim than "$217,454".
**Would have helped:** a `--sum` / `--total` on `usaspending` that returns total obligated across ALL
matching awards, not just the displayed page. For a procurement-heavy beat this is the single most
common question asked of the tool and it is currently the one thing it will not answer directly.
**Severity:** slowed (≈10 regex-and-sum detours), and it degraded the output's evidentiary strength.

**Worked well — and specifically, this is what cracked the ticket.**

`usaspending` recipient-name search with the `VerifiedAbsence` vs `Hit` distinction is what made this pass
succeed where five prior passes failed. Concretely:

```
$CS usaspending "Calibre Press" --count --wait     -> VerifiedAbsence
$CS usaspending "Street Survival" --count --wait   -> VerifiedAbsence
$CS usaspending "Lifeline Training" --count --wait -> Hit, 27 awards
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
ERRORED: searxng:startpage`) and **refusing to certify absence** on the Mike Hanson query at 78/82. My
dispatch prompt specifically warned me not to write tooling-limited negatives up as content-exhausted
ones, and the tool enforced that for me rather than leaving it to my discipline. That is the correct
division of labour and it directly changed what I wrote (Hanson logged as unresolved-retry, not as a
negative finding).

**Severity summary:** Friction 1 — slowed. Friction 2 — slowed, near-miss on a wrong conclusion.
Friction 3 — slowed, degraded output strength. Net: the tool won the ticket; the friction is all in
getting aggregates and in empty-result presentation.

## 2026-08-19 · brief-write-draft-series-2-keystone-panopticon-was-the-wrong-metaphor-to-v1 · claude-sonnet-5

**Context:** claimed a "brief-write/draft" task instructing me to write a 4,500-6,000w
RAMM keystone brief on the panopticon-vs-Agre-capture frame correction. Did not end up
needing cascade-search at all — the actual finding was that the deliverable already
existed (fact-checked, publish-audited, with-Amy since 2026-06-11), so the task closed
as a stale-ticket discovery rather than a research task. Logging this because the
absence of cascade-search usage is itself informative for triage, and because the
discovery method is a `~/kb/kb search` / Pyrite task-list finding worth naming.

**Command:** `~/kb/kb search "panopticon wrong metaphor keystone" -k drafts --limit 10`

**Expected:** a ranked list of loosely-related theme/draft entries to orient on.

**Got:** the top hit (`dc-launch-order-checklist`) contained the exact answer in its
snippet — a highlighted `with-amy · fc complete` status line with a live Drive-doc
link for the precise piece the task asked me to write. One search call resolved the
whole task. This is `~/kb/kb search`, not cascade-search, but it is the tool that
actually mattered for this ticket and it worked well: FTS ranking surfaced a launch
checklist over dozens of theme/task files that also mention "panopticon."

**Friction:** none in the tooling. The friction was upstream, in the task itself —
the claimed ticket's body asserted "the brief EXISTS but is not yet drafted" as
settled fact, sourced to a 2026-06-11 brief-gap survey. Two *other* task files for
the identical deliverable (`write-keystone-brief-series-2-...` and
`brief-write-the-panopticon-was-the-wrong-metaphor-...`) were both already `status:
done` in the same `~/kb/kb task list` output space, and the actual draft file had
been sitting in `drafts/drafts/` with a completed audit trail for over two months.
Nothing in `~/kb/kb task list --status open` flagged this as likely-redundant before
I claimed it — I only found out by reading the task body, then checking the
`Produces:` path by hand.

**Would have helped:** if task claiming (or the investigation-research skill's Step 1
picker) did a cheap pre-check — does the `Produces:` path, or an obvious slug match,
already exist as a file? — before surfacing a task as open. This is a Pyrite/
task-hygiene gap, not a cascade-search gap, but it cost real wall-clock: I read three
overlapping task files and a 398-line draft plus its audit sidecar before concluding
"do nothing, close it." A worker with less discipline than "verify the Produces: path
before writing" could easily have shipped a second competing draft against a piece
already sitting in Amy's editorial queue.

**Worked well:** `~/kb/kb search` scoped to `-k drafts` surfaced the ground-truth
status in the first hit. The audit-sidecar convention (separate `.audit.yaml` with
fact_check_log / publish_audit / revision_log / pre_publish_blockers) made it fast to
confirm the draft was not a rough stub — it had already cleared fact-check and a
publish audit, which is what made "close as done, do not duplicate" the confident
right call rather than a guess.

**Severity:** slowed (on the task-hygiene issue, not on any search tool). No
cascade-search friction to report this pass — clean non-use.

## 2026-08-19 · pull-psc-naics-course-titles-on-all-27-lifeline-training-awards · claude-sonnet-5

**Context:** ticket asked for PSC + NAICS + course-title/description on all 27 of Lifeline
Training Ltd's federal prime contracts, testing whether any repeats the <vendor-b> ICE
pattern (protective-purpose title against combat-coded PSC U013). Needed the actual
`product_or_service_code` field on every award, not just titles.

**Command 1:** `$CS --wait usaspending "Lifeline Training" --group contracts --limit 27 --json`

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
