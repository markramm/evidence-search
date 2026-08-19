"""Google News RSS — free, keyless, and it worked when WebSearch was exhausted.

Not a general web index, but a genuine search endpoint over news, at zero cost
and with no key. On 2026-08-19 this was the only search that ran after the
session's 200-call WebSearch budget was gone.
"""
from __future__ import annotations

import time
from urllib.parse import quote
from xml.etree import ElementTree as ET

from ..core.http import fetch
from ..core.limits import Limiter
from ..core.results import Coverage, Hit, RateLimited, Result, verified_absence
from ..core.store import Store, cache_key

ENDPOINT = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
INDEX_ORIGIN = "google"


def search(query: str, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    store = store or Store()
    limiter = limiter or Limiter(store)

    key = cache_key("news_rss", query)
    if use_cache:
        cached = store.get(key)
        if cached is not None:
            cov = Coverage(queried=["news_rss"], responsive=["news_rss"],
                           indexes=[INDEX_ORIGIN], cache_hits=1)
            return Hit(query=query, coverage=cov, results=[Result(**r) for r in cached]) \
                if cached else verified_absence(query, cov, "google-news-rss")

    allowed, retry, why = limiter.check("news_rss")
    if not allowed:
        return RateLimited(query=query, coverage=Coverage(queried=["news_rss"], rate_limited=["news_rss"]),
                           source="news_rss", retry_after_s=int(retry) if retry else None, detail=why)

    t0 = time.time()
    body, blocked = fetch(ENDPOINT.format(q=quote(query)), source="news_rss", query=query)
    limiter.record("news_rss")
    if blocked:
        blocked.coverage = Coverage(queried=["news_rss"], errored={"news_rss": "blocked"})
        return blocked

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        return RateLimited(query=query, coverage=Coverage(queried=["news_rss"],
                                                          errored={"news_rss": "bad-xml"}),
                           source="news_rss", detail=str(e))

    cov = Coverage(queried=["news_rss"], responsive=["news_rss"], indexes=[INDEX_ORIGIN],
                   elapsed_ms=int((time.time() - t0) * 1000))
    items = root.findall(".//item")
    if not items:
        if use_cache:
            store.put(key, "news_rss", [], ttl_s=3600)
        return verified_absence(query, cov, "google-news-rss")

    out = []
    for it in items:
        def txt(tag):
            el = it.find(tag)
            return el.text if el is not None and el.text else ""
        src = it.find("source")
        out.append(Result(
            url=txt("link"), title=txt("title"), snippet=txt("pubDate"),
            source="news_rss", engines=["news_rss"], index_origin=[INDEX_ORIGIN],
            meta={"published": txt("pubDate"),
                  "publisher": src.text if src is not None else ""},
        ))
    if use_cache:
        store.put(key, "news_rss", [r.__dict__ for r in out], ttl_s=3600)
    return Hit(query=query, coverage=cov, results=out)
