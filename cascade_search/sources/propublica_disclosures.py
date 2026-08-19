"""ProPublica — Trump Team Financial Disclosures.

Reverse-engineered 2026-08-19. The public page is a SvelteKit app, so the HTML
contains no results -- but SvelteKit exposes its loader data at __data.json:

    /trump-team-financial-disclosures/search/__data.json?q=<query>

IMPORTANT: the parameter is `q`, NOT `search`. Using `search=` silently returns
the UNFILTERED index (1,607 rows) with q='' -- i.e. it looks like a successful
broad result rather than an error. That is exactly the kind of silent-wrong
answer this tool exists to prevent, so we verify the echoed `q` matches what we
asked for and downgrade to a blocker if it does not.

SvelteKit flattens its payload into an index-referenced array: values may be
integers pointing at other slots. deref() walks that.

Covers ~1,600 appointees. Searching an asset name (e.g. "Blue Owl") returns
every appointee disclosing it, with agency aggregations.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from ..core.limits import Limiter
from ..core.results import Result
from ..core.source import run_source
from ..core.store import Store

BASE = "https://projects.propublica.org/trump-team-financial-disclosures"
SOURCE = "propublica_disclosures"


def _deref(arr: list, value, depth: int = 0):
    """Resolve SvelteKit's index-referenced payload into plain data."""
    if depth > 10:
        return None
    if isinstance(value, int) and 0 <= value < len(arr):
        return _deref(arr, arr[value], depth + 1)
    if isinstance(value, dict):
        return {k: _deref(arr, v, depth + 1) for k, v in value.items()}
    if isinstance(value, list):
        return [_deref(arr, v, depth + 1) for v in value]
    return value


def _rows_and_echo(body: str):
    """Resolve the SvelteKit payload into (rows, echoed_query)."""
    try:
        data = json.loads(body)
        arr = [n for n in data["nodes"] if isinstance(n, dict) and n.get("type") == "data"][0]["data"]
        top = arr[0]
        return _deref(arr, top.get("result")) or [], _deref(arr, top.get("q"))
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        raise ValueError(f"ProPublica payload shape changed: {e}") from e


def _verify(query: str):
    """A wrong param name returns the UNFILTERED 1,607-row index with q=''.

    That looks like a broad success. Refuse it: confirm the server echoed our
    query back before trusting anything it sent.
    """
    def check(body: str):
        try:
            _, echoed = _rows_and_echo(body)
        except ValueError:
            return None  # let parse() report the shape change
        if (echoed or "").strip().lower() != query.strip().lower():
            return (f"server echoed q={echoed!r} for query {query!r}: the filter was "
                    "NOT applied. Results would be the unfiltered index. "
                    "Refusing to return them.")
        return None
    return check


def _parser(query: str):
    """Build a parser bound to this query (for the no-slug fallback URL).

    Deliberately a closure, not a module global: concurrent searches in one
    process would race on shared mutable state.
    """
    fallback_url = f"{BASE}/?search={quote(query)}"

    def _parse(body: str) -> list[Result]:
        rows, _ = _rows_and_echo(body)
        out = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            name = r.get("a_txt") or ""
            slug = r.get("a_slug") or ""
            agency = r.get("agency_name") or ""
            title = r.get("title") or ""
            nw = r.get("net_worth_low")

            # `highlights` carries the matched asset text with <mark> tags -- this is
            # WHY the appointee matched, and the most useful field for a reporter.
            matched: list[str] = []
            raw_hl = r.get("highlights")
            if raw_hl:
                try:
                    hl = json.loads(raw_hl) if isinstance(raw_hl, str) else raw_hl
                    for v in (hl or {}).values():
                        if v:
                            matched.append(re.sub(r"</?mark>", "", str(v)))
                except (json.JSONDecodeError, AttributeError):
                    pass

            bits = [b for b in (agency, title) if b]
            if nw:
                try:
                    bits.append(f"net worth from ${int(nw):,}")
                except (TypeError, ValueError):
                    bits.append(f"net worth from {nw}")
            if matched:
                bits.append(f"matched: {matched[0][:120]}")

            out.append(Result(
                url=f"{BASE}/appointee/{slug}" if slug else fallback_url,
                title=name or slug or "(unnamed appointee)",
                snippet=" | ".join(bits),
                source=SOURCE, engines=[SOURCE], index_origin=["n/a"],
                meta={"appointee": name, "slug": slug, "agency": agency, "title": title,
                      "net_worth_low": nw, "matched_assets": matched,
                      "earliest_document": r.get("document_received_date_earliest")},
            ))
        return out

    return _parse


def search(query: str, store: Store | None = None, limiter: Limiter | None = None,
           use_cache: bool = True):
    """Search disclosures by asset, entity, or appointee name."""
    return run_source(
        query, source=SOURCE,
        url=f"{BASE}/search/__data.json?q={quote(query)}",
        searched="propublica trump-team financial disclosures (~1,600 appointees)",
        parse=_parser(query), verify=_verify(query),
        store=store, limiter=limiter, use_cache=use_cache,
    )
