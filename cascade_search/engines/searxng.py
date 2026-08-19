"""SearXNG — the general-web gap, on our terms.

SearXNG is 251 maintained engine scrapers behind one JSON API. That catalogue
is the asset: it closes the "asked an open-ended question, got the wrong thing"
gap without us writing and maintaining scrapers for Google, Bing, and Marginalia.

What we do NOT adopt is its failure semantics. Two things in its source are
disqualifying for a tool whose product is a defensible negative:

  * `json_engine.py:400` returns an EMPTY LIST when an engine gets an HTTP
    status in `no_result_for_http_status`. A blocked engine and an engine that
    genuinely found nothing are indistinguishable in the results array.

  * failures are tracked in a PARALLEL channel (`unresponsive_engines`) that an
    operator can switch off per engine via `display_error_messages`. A
    suppressed failure vanishes entirely.

So this adapter treats the results array as untrusted on its own, and rebuilds
Coverage from the two facts the API does expose honestly:

    queried    = engines enabled on the instance      (/config, cached)
    failed     = unresponsive_engines                 (per search)
    responsive = queried - failed

An absence is then only publishable when EVERY enabled engine answered. A
search where 3 of 8 engines were suspended cannot certify that a thing does not
exist -- which is the guarantee `verified_absence()` enforces, now extended
across a federated tier.

Scoring is ours, not theirs. `calculate_score` in searx/results.py multiplies
weight by len(positions), so a result four engines agree on scores 4x. That is
consensus ranking, and on this beat it buries the obscure trade-press hit or
agency subpage that only one index carries. We keep SearXNG's per-result
`engines` set and let `unique_to_engine` mark those as worth a look.
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import quote

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, RateLimited,
                            Result, replay_cached, verified_absence)
from ..core.store import Store, cache_key

SOURCE = "searxng"
DEFAULT_BASE = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")

# --- failure classification ---------------------------------------------------
#
# SearXNG does NOT expose the stable identifier. `UnresponsiveEngine` carries
# `error_type` (an exception class name) and `suspended` (a bool), but
# `get_translated_errors` in searx/webutils.py collapses both into ONE
# gettext()-translated string before the JSON is built:
#
#     error_msg = gettext(exception_classname_to_text[e.error_type])
#     if e.suspended: error_msg = gettext('Suspended') + ': ' + error_msg
#
# So the only signal that reaches us is prose, in whatever locale the request
# negotiated. Verified against a live instance: an Accept-Language of de-DE
# turns "too many requests" into "zu viele Anfragen" and "Suspended:" into
# "Ausgesetzt:". SearXNG ships 60 locales, so string-matching English alone is
# a silent misclassification waiting to happen.
#
# Three defenses, in order:
#   1. PIN the locale. We send `locale=en` and `Accept-Language: en-US` so the
#      instance answers in the language we can read. This is the real fix.
#   2. Match the English strings from `exception_classname_to_text`, plus the
#      few non-English forms most likely to survive a misconfigured instance.
#   3. NEVER guess. An unrecognised message is recorded as UNKNOWN and dirties
#      coverage, so it can still never certify an absence -- but it is labelled
#      as unclassified rather than silently filed as a hard error.

#: Throttling: tooling-limited, explicitly not content-exhausted.
_RATE_LIMIT_SIGNALS = (
    "too many requests", "timeout", "timed out",
    # a suspended engine is one SearXNG itself backed off from
    "suspended",
    # highest-traffic locales, as insurance against an instance that ignores
    # our pinned locale
    "zu viele anfragen", "trop de requêtes", "demasiadas peticiones",
    "ausgesetzt", "suspendu", "suspendido",
)

#: A wall: the door was shut, which is not an empty shelf.
_BLOCK_SIGNALS = (
    "captcha", "access denied", "http error", "connection error",
    "protocol error", "network error", "proxy error", "ssl error",
    "server api error", "parsing error", "unexpected crash",
    "zugriff verweigert", "accès refusé", "acceso denegado",
)

_CONFIG_TTL = 3600


def _enabled_engines(base: str, store: Store) -> list[str]:
    """Engines the instance will actually query. Cached: it changes at deploy time.

    Without this the results array alone cannot distinguish "nobody found it"
    from "nobody was asked".
    """
    # v2: the namespacing change altered the SHAPE of this cached value, and a
    # stale v1 entry (bare engine names) silently broke the responsive-count
    # arithmetic -- 82/82 responsive while two engines were failing. Version the
    # key so a format change can never be served a stale entry.
    key = cache_key(f"{SOURCE}:config:v2", base)
    hit = store.get(key)
    if hit is not None:
        return hit
    body, blocked = fetch(f"{base}/config", source=SOURCE, query="config")
    if blocked or not body:
        return []
    try:
        cfg = json.loads(body)
    except json.JSONDecodeError:
        return []
    names = sorted(f"searxng:{e['name']}" for e in cfg.get("engines", []) if e.get("enabled", True))
    store.put(key, SOURCE, names, ttl_s=_CONFIG_TTL)
    return names


def _classify(errors: list) -> tuple[list[str], dict[str, str], list[str]]:
    """Split unresponsive engines into (rate_limited, errored, unclassified).

    Returns the unclassified names too, so a message we cannot read is visible
    as such rather than being quietly filed under a category we guessed at.
    Either way it dirties coverage: an unreadable failure is still a failure.
    """
    # Namespaced `searxng:<engine>`, because these are UPSTREAM engines the
    # instance proxies -- NOT sources in our own ledger. A live worker read
    # "RATE-LIMITED: brave", checked `cascade-search limits`, saw brave at
    # 0/20, and reasonably concluded the tool was contradicting itself. Our
    # limiter governs calls WE make; this governs what Brave did to SearXNG.
    # Same word, two namespaces, no way to tell them apart without the prefix.
    rate_limited: list[str] = []
    errored: dict[str, str] = {}
    unknown: list[str] = []

    for item in errors or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            name, msg = str(item[0]), str(item[1])
        else:
            name, msg = str(item), ""
        low = msg.lower()

        # Check blocks FIRST: "Suspended: CAPTCHA" is a wall the instance has
        # additionally backed off from, and the wall is the more actionable
        # fact -- a CAPTCHA will not clear itself by waiting.
        tag = f"searxng:{name}"
        if any(sig in low for sig in _BLOCK_SIGNALS):
            errored[tag] = msg
        elif any(sig in low for sig in _RATE_LIMIT_SIGNALS):
            rate_limited.append(tag)
        else:
            unknown.append(tag)
            errored[tag] = f"unclassified: {msg}" if msg else "unclassified"

    return rate_limited, errored, unknown


def _exact_terms(query: str) -> list[str]:
    """Quoted phrases in the query, which the caller meant literally."""
    import re as _re
    return [t for t in _re.findall(r'"([^"]{2,})"', query)]


def search(query: str, categories: str = "general", pageno: int = 1,
           base: str | None = None, store: Store | None = None,
           limiter: Limiter | None = None, use_cache: bool = True,
           exact: bool = False):
    """Query a SearXNG instance and rebuild honest coverage from its metadata.

    `exact=True` keeps only results whose title or snippet actually contains
    every quoted phrase. The upstream engines largely IGNORE quotes -- verified
    against a live instance, where `"<person-h>"` and `<person-h>` returned
    26 and 28 results with the same near-miss profile matches on top. Two workers
    hit this on person-name searches and had no way to say "this exact string or
    nothing", which matters most for the case a name search is usually FOR:
    establishing that someone is genuinely absent from an index.

    We cannot make the engines honour quotes, so we filter locally and report
    what was dropped rather than pretending the upstream did it.
    """
    store = store or Store()
    limiter = limiter or Limiter(store)
    base = (base or DEFAULT_BASE).rstrip("/")

    key = cache_key(SOURCE, query, categories=categories, pageno=pageno, base=base)
    if use_cache:
        entry = store.get_entry(key)
        if entry is not None:
            return replay_cached(query, entry[0], entry[1], source=SOURCE,
                                 searched=f"searxng ({categories})", index_origin="mixed")

    allowed, retry, why = limiter.reserve(SOURCE)
    if not allowed:
        return RateLimited(query=query, coverage=Coverage(queried=[SOURCE], rate_limited=[SOURCE]),
                           source=SOURCE, retry_after_s=int(retry) if retry else None, detail=why)

    t0 = time.time()
    # Pin the locale: the failure channel is prose, and we must be able to read
    # it. Without this the instance answers in whatever the request negotiated.
    url = (f"{base}/search?q={quote(query)}&format=json"
           f"&categories={quote(categories)}&pageno={pageno}&locale=en")
    body, blocked = fetch(url, source=SOURCE, query=query,
                          headers={"Accept-Language": "en-US,en;q=0.9"})
    if blocked:
        blocked.coverage = Coverage(queried=[SOURCE], errored={SOURCE: "blocked"},
                                    elapsed_ms=int((time.time() - t0) * 1000))
        detail = str(getattr(blocked, "detail", ""))
        if "refused" in detail.lower() or getattr(blocked, "mechanism", None) is Blocker.NOT_FOUND:
            blocked.detail = (
                f"No SearXNG instance at {base} ({detail}). Start one with:\n"
                "    deploy/searxng-native.sh up    # no container runtime needed\n"
                "    deploy/searxng.sh up           # containerised, if you prefer\n"
                "Point elsewhere with SEARXNG_URL.")
        return blocked

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return AccessBlocker(
            query=query, coverage=Coverage(queried=[SOURCE], errored={SOURCE: "non-json"}),
            mechanism=Blocker.SERVER_ERROR, url=url,
            detail=("Instance did not return JSON. SearXNG ships with the json "
                    "format DISABLED; add `formats: [html, json]` under `search:` "
                    "in settings.yml."))

    rate_limited, errored, unclassified = _classify(data.get("unresponsive_engines"))
    enabled = _enabled_engines(base, store)
    failed = set(rate_limited) | set(errored)
    # Fall back to the engines that actually returned rows if /config is
    # unavailable -- degraded, but never silently optimistic.
    queried = enabled or sorted({f"searxng:{e}" for r in data.get("results", [])
                                 for e in (r.get("engines") or [])} | failed)
    responsive = [e for e in queried if e not in failed]

    cov = Coverage(queried=queried, responsive=responsive,
                   rate_limited=rate_limited, errored=errored,
                   indexes=["mixed"], elapsed_ms=int((time.time() - t0) * 1000))
    if unclassified:
        # Loud, not silent: if this fires, the instance is answering in a locale
        # we did not expect and the signal map needs updating.
        print(f"cascade-search: unclassified searxng failure(s) for "
              f"{', '.join(unclassified)} -- treated as errors, coverage dirty. "
              f"Is the instance ignoring `locale=en`?", file=sys.stderr)

    out = []
    for r in data.get("results", []):
        engines = sorted(r.get("engines") or ([r["engine"]] if r.get("engine") else []))
        out.append(Result(
            url=r.get("url", ""), title=r.get("title", ""),
            snippet=(r.get("content") or "")[:400],
            source=SOURCE, engines=engines, index_origin=["mixed"],
            # `positions` is a list of ranks and `engines` a set of names; they
            # are parallel only by convention, so pair them positionally and
            # only when the lengths agree. Guessing would invent provenance.
            rank_by_engine=(dict(zip(engines, r["positions"]))
                            if len(r.get("positions") or []) == len(engines) else {}),
            meta={"published": r.get("publishedDate"), "category": r.get("category"),
                  "searxng_score": r.get("score"), "engine": r.get("engine")},
        ))

    if use_cache and cov.is_clean:
        # Only cache a CLEAN sweep. Caching a partial one would replay a
        # degraded search as though every engine had answered.
        store.put(key, SOURCE, [x.__dict__ for x in out], ttl_s=3600)

    dropped = 0
    if exact:
        terms = _exact_terms(query)
        if terms:
            kept = []
            for r in out:
                hay = f"{r.title} {r.snippet}".lower()
                if all(t.lower() in hay for t in terms):
                    kept.append(r)
            dropped = len(out) - len(kept)
            out = kept
            cov.exact_filtered = dropped

    if not out:
        searched = f"searxng ({categories}) across {len(queried)} engines"
        if exact and dropped:
            searched += (f" [exact-phrase filter dropped all {dropped} near-miss "
                         "results; the phrase itself appears in none of them]")
        return verified_absence(query, cov, searched)
    return Hit(query=query, coverage=cov, results=out)
