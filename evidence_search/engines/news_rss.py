"""Google News RSS — free, keyless, and it worked when WebSearch was exhausted.

Not a general web index, but a genuine search endpoint over news, at zero cost
and with no key. On 2026-08-19 this was the only search that ran after the
session's 200-call WebSearch budget was gone.
"""
from __future__ import annotations

from urllib.parse import quote
from xml.etree import ElementTree as ET

from ..core.limits import Limiter
from ..core.results import Result
from ..core.source import RecognizedEmpty, run_source
from ..core.store import Store

ENDPOINT = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
INDEX_ORIGIN = "google"
SOURCE = "news_rss"


def _parse(body: str) -> list[Result]:
    """Parse the RSS feed.

    A warning about the URLs: Google News hands back a ~320-character opaque
    redirect, never the publisher's own link. The real URL is not in the feed at
    all, and the redirect returns HTTP 400 to automated clients (it wants a
    browser session), so it cannot be cheaply resolved. Two workers reported
    this independently -- one called the news pass "effectively unusable for the
    deliverable" because a citation cannot be an opaque Google redirect.

    What the feed DOES carry is the publisher name. So each result is marked
    `citable: False` with the publisher surfaced, which is enough to go find the
    piece on the publisher's own site. Treat `news` as a DISCOVERY index: it
    tells you a story exists and who ran it. Get the citable URL elsewhere --
    `web` usually has the same story with a clean link.
    """
    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise ValueError(f"bad RSS/XML: {e}") from e

    # The feed must be a feed. Well-formed XML that is not <rss><channel> -- an
    # error document, an XHTML consent or challenge page -- has no <item>s
    # either, and that used to be an absence of news.
    channel = root.find("channel") if root.tag == "rss" else None
    if channel is None:
        raise ValueError(f"not an RSS feed (root <{root.tag}>, no <channel>)")
    items = channel.findall("item")
    if not items:
        return RecognizedEmpty("rss channel with no items")

    out = []
    for it in items:
        def txt(tag):
            el = it.find(tag)
            return el.text if el is not None and el.text else ""
        src = it.find("source")
        out.append(Result(
            url=txt("link"), title=txt("title"), snippet=txt("pubDate"),
            source=SOURCE, engines=[SOURCE], index_origin=[INDEX_ORIGIN],
            meta={"published": txt("pubDate"),
                  "publisher": src.text if src is not None else "",
                  "citable": False,
                  "url_note": ("Google News redirect, not the publisher's URL. "
                               "It 400s to automated clients. Find the piece on "
                               f"{src.text if src is not None else 'the publisher'}'s "
                               "own site, or via `evidence-search web`, before citing.")},
        ))
    return out


def search(query: str, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    return run_source(
        query, source=SOURCE,
        url=ENDPOINT.format(q=quote(query)),
        searched="google-news-rss",
        parse=_parse, store=store, limiter=limiter, use_cache=use_cache,
        index_origin=INDEX_ORIGIN, ttl_s=3600,
        # An empty feed is an absence from ONE news index's search feed, and
        # used to be reported with no caveats at all ("Not found").
        exact_match_supported=False,
        corpus=("Google News RSS search feed: one page of recent news items, "
                "from the publishers Google News indexes."),
        not_searched=["the general web (use `web`)",
                      "publishers Google News does not index",
                      "older coverage outside what the feed returns",
                      "court, contract and disclosure records"],
        caveats=["DISCOVERY tier: one news index, one page of its feed, with "
                 "Google's own query interpretation. No news in this feed is not "
                 "'no coverage exists'; do not publish it as an absence without "
                 "a second source."],
    )
