---
name: New source request
about: Propose a database or search target worth a client
labels: source-request
---

## What research question does this answer?

The question a researcher is actually asking when they reach for it. Not the
database's name — the thing they need to know. This is what tells an implementer
when the client is done.

## Access shape

- [ ] Keyless JSON API
- [ ] API needing a key (who issues it, is it free, rate limits)
- [ ] Undocumented-but-unauthenticated endpoint (paste the URL that worked)
- [ ] Bulk download / dataset
- [ ] Interactive only (likely an `AwaitingHuman` gate, not a client)
- [ ] Unknown

## Evidence it works

A `curl` that returned something, or the URL of a page that has the data. A
source we cannot reach today is still worth filing — say so explicitly.

## Does it support exact-phrase matching?

Load-bearing for `VerifiedAbsence`. If the corpus cannot do exact match, an
absence from it is bounded, not absolute — the client must report that.

## What would a verified absence from this source mean?

If searching it properly and finding nothing is NOT publishable, say why.
