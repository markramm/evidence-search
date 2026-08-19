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
import time
from urllib.parse import quote

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import (AccessBlocker, Blocker, Coverage, Hit, RateLimited,
                            Result, replay_cached, verified_absence)
from ..core.store import Store, cache_key

SOURCE = "searxng"
DEFAULT_BASE = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")

#: Error text from searx/webutils.py `exception_classname_to_text`. These mean
#: "the engine was throttled or banned" -- tooling-limited, explicitly not
#: content-exhausted, and a retry is warranted.
_RATE_LIMIT_SIGNALS = ("too many requests", "suspended", "timeout", "timed out")

#: These mean the door was shut: a wall, not an empty shelf.
_BLOCK_SIGNALS = ("captcha", "access denied", "http error", "connection error",
                  "proxy error", "unexpected crash", "parsing error", "server api error")

_CONFIG_TTL = 3600


def _enabled_engines(base: str, store: Store) -> list[str]:
    """Engines the instance will actually query. Cached: it changes at deploy time.

    Without this the results array alone cannot distinguish "nobody found it"
    from "nobody was asked".
    """
    key = cache_key(f"{SOURCE}:config", base)
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
    names = sorted(e["name"] for e in cfg.get("engines", []) if e.get("enabled", True))
    store.put(key, SOURCE, names, ttl_s=_CONFIG_TTL)
    return names


def _classify(errors: list) -> tuple[list[str], dict[str, str]]:
    """Split SearXNG's unresponsive engines into rate-limited vs errored.

    The API hands back [engine, human_message] pairs, already translated. We
    match on the English source strings from `exception_classname_to_text`.
    """
    rate_limited: list[str] = []
    errored: dict[str, str] = {}
    for item in errors or []:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            name, msg = str(item[0]), str(item[1])
        else:
            name, msg = str(item), "unresponsive"
        low = msg.lower()
        if any(s in low for s in _RATE_LIMIT_SIGNALS):
            rate_limited.append(name)
        else:
            errored[name] = msg if any(s in low for s in _BLOCK_SIGNALS) else msg
    return rate_limited, errored


def search(query: str, categories: str = "general", pageno: int = 1,
           base: str | None = None, store: Store | None = None,
           limiter: Limiter | None = None, use_cache: bool = True):
    """Query a SearXNG instance and rebuild honest coverage from its metadata."""
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
    url = (f"{base}/search?q={quote(query)}&format=json"
           f"&categories={quote(categories)}&pageno={pageno}")
    body, blocked = fetch(url, source=SOURCE, query=query)
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

    rate_limited, errored = _classify(data.get("unresponsive_engines"))
    enabled = _enabled_engines(base, store)
    failed = set(rate_limited) | set(errored)
    # Fall back to the engines that actually returned rows if /config is
    # unavailable -- degraded, but never silently optimistic.
    queried = enabled or sorted({e for r in data.get("results", [])
                                 for e in (r.get("engines") or [])} | failed)
    responsive = [e for e in queried if e not in failed]

    cov = Coverage(queried=queried, responsive=responsive,
                   rate_limited=rate_limited, errored=errored,
                   indexes=["mixed"], elapsed_ms=int((time.time() - t0) * 1000))

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

    if not out:
        return verified_absence(query, cov, f"searxng ({categories}) across "
                                            f"{len(queried)} engines")
    return Hit(query=query, coverage=cov, results=out)
