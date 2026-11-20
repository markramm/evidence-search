"""Zero-daemon general web search client (`evidence-search web`).

Replaces a local SearXNG daemon as the default `web` backend while preserving
SearXNG as an explicit opt-in override (`--provider searxng`, `--base`, or
`$SEARXNG_URL`).

Provider resolution (`--provider auto`, the default):
  1. Explicit `--base` or `$SEARXNG_URL` -> delegates to ``engines.searxng``.
  2. `$BRAVE_API_KEY`                    -> Brave Web Search REST API.
  3. `$SERPER_API_KEY`                   -> Serper Google Search REST API.
  4. `$TAVILY_API_KEY`                   -> Tavily Search REST API.
  5. Otherwise (zero-config default)     -> Mwmbl keyless JSON API.

Mwmbl returns a bare JSON list `[]` on zero hits, so `_parse_mwmbl` refuses to
certify an absence on `[]` unless a positive control query (`s=wikipedia`)
succeeds — preventing an empty/degraded error array from masquerading as a
`VerifiedAbsence`.
"""
from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode

import httpx

from ..core.http import detect_blocker, fetch, retry_after_seconds
from ..core.limits import Limiter
from ..core.registry import spec_bounds
from ..core.results import (
    AccessBlocker,
    Blocker,
    Coverage,
    Outcome,
    RateLimited,
    Result,
)
from ..core.source import RecognizedEmpty, run_source
from ..core.store import Store, normalize_url

SOURCE = "web"

MWMBL_URL = "https://api.mwmbl.org/api/v1/search/"
BRAVE_URL = "https://api.search.brave.com/res/v1/web/search"
SERPER_URL = "https://google.serper.dev/search"
TAVILY_URL = "https://api.tavily.com/search"

PROVIDERS = ("auto", "mwmbl", "brave", "serper", "tavily", "searxng")
_KEY_ENV: dict[str, str] = {
    "brave": "BRAVE_API_KEY",
    "serper": "SERPER_API_KEY",
    "tavily": "TAVILY_API_KEY",
}
_PROVIDER_URL: dict[str, str] = {
    "mwmbl": MWMBL_URL,
    "brave": BRAVE_URL,
    "serper": SERPER_URL,
    "tavily": TAVILY_URL,
}

CORPUS, NOT_SEARCHED, STANDING_CAVEATS = spec_bounds(
    SOURCE,
    corpus="General web search (zero-daemon keyless Mwmbl default; optional Brave/Serper/Tavily keys or SearXNG override).",
    not_searched=(
        "paywalled or login-walled sites",
        "deep-web databases not indexed by general web engines (PACER, state SOS postbacks)",
    ),
    caveats=(
        "Upstream web engines largely ignore quoted phrases; result sets are fuzzy and totals are not countable.",
        "General web results are discovery leads rather than an exhaustive census.",
    ),
)


def resolve_provider(provider: str = "auto", base: str | None = None) -> str:
    """Resolve which backend fulfills a `web` search call."""
    prov = (provider or "auto").strip().lower()
    if prov != "auto":
        if prov not in PROVIDERS:
            raise ValueError(
                f"Unknown web provider {provider!r}; expected one of {', '.join(PROVIDERS)}"
            )
        return prov
    if (base or "").strip() or os.environ.get("SEARXNG_URL", "").strip():
        return "searxng"
    for candidate in ("brave", "serper", "tavily"):
        if os.environ.get(_KEY_ENV[candidate], "").strip():
            return candidate
    return "mwmbl"


def _flatten_mwmbl_spans(val: Any) -> str:
    """Flatten Mwmbl highlight span arrays (`[{"value": ..., "is_bold": ...}]`) into text."""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, list):
        parts: list[str] = []
        for item in val:
            if isinstance(item, dict):
                parts.append(str(item.get("value") or ""))
            elif item is not None:
                parts.append(str(item))
        return "".join(parts).strip()
    return str(val).strip() if val is not None else ""


def _dedup_results(rows: list[Result], limit: int) -> list[Result]:
    out: list[Result] = []
    seen: set[str] = set()
    cap = max(1, int(limit))
    for item in rows:
        key = normalize_url(item.url)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= cap:
            break
    return out


def _verify_mwmbl_available(query: str) -> bool:
    """Confirm Mwmbl is returning live search rows before certifying `[]` as an absence."""
    ctrl = "python" if query.strip().lower() == "wikipedia" else "wikipedia"
    ctrl_url = f"{MWMBL_URL}?{urlencode({'s': ctrl})}"
    body, blocked = fetch(ctrl_url, source=SOURCE, query=f"ctrl:{ctrl}")
    if blocked is not None or not body:
        return False
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return False
    return (
        isinstance(data, list)
        and len(data) > 0
        and isinstance(data[0], dict)
        and bool(data[0].get("url"))
    )


def _parse_mwmbl(
    body: str,
    *,
    limit: int = 25,
    verify_empty_fn=None,
) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"Mwmbl returned non-JSON: {e}") from e

    if not isinstance(data, list):
        raise ValueError(f"Mwmbl returned unexpected JSON type {type(data).__name__}")

    if len(data) == 0:
        if verify_empty_fn is not None and verify_empty_fn():
            return RecognizedEmpty("mwmbl: [] verified")
        raise ValueError("Mwmbl returned bare [] without availability verification")

    valid_rows = [r for r in data if isinstance(r, dict) and str(r.get("url") or "").strip()]
    if not valid_rows:
        raise ValueError("Mwmbl rows missing expected 'url' field")

    parsed = [
        Result(
            url=str(r["url"]).strip(),
            title=_flatten_mwmbl_spans(r.get("title")),
            snippet=_flatten_mwmbl_spans(r.get("extract")),
            source=SOURCE,
            engines=["mwmbl"],
            index_origin=["mwmbl"],
            meta={
                "provider": "mwmbl",
                "upstream": str(r.get("source") or "mwmbl"),
            },
        )
        for r in valid_rows
    ]
    return _dedup_results(parsed, limit)


def _parse_brave(body: str, *, limit: int = 25) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"Brave Search returned non-JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Brave Search returned unexpected JSON type {type(data).__name__}")

    web_obj = data.get("web")
    if isinstance(web_obj, dict) and isinstance(web_obj.get("results"), list):
        rows = web_obj["results"]
    elif data.get("type") == "search" and isinstance(data.get("query"), dict) and web_obj is None:
        return RecognizedEmpty("brave: search with no web results")
    else:
        raise ValueError("Brave Search payload missing expected 'web.results' list")

    if len(rows) == 0:
        return RecognizedEmpty("brave: web.results=[]")

    valid_rows = [r for r in rows if isinstance(r, dict) and str(r.get("url") or "").strip()]
    if not valid_rows:
        raise ValueError("Brave Search rows missing expected 'url' field")

    parsed = [
        Result(
            url=str(r["url"]).strip(),
            title=str(r.get("title") or "").strip(),
            snippet=str(r.get("description") or "").strip(),
            source=SOURCE,
            engines=["brave"],
            index_origin=["brave"],
            meta={
                "provider": "brave",
                "age": str(r.get("age") or "").strip(),
            },
        )
        for r in valid_rows
    ]
    return _dedup_results(parsed, limit)


def _parse_serper(body: str, *, limit: int = 25) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"Serper returned non-JSON: {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("organic"), list):
        raise ValueError("Serper payload missing expected 'organic' list")

    rows = data["organic"]
    if len(rows) == 0:
        return RecognizedEmpty("serper: organic=[]")

    valid_rows = [r for r in rows if isinstance(r, dict) and str(r.get("link") or "").strip()]
    if not valid_rows:
        raise ValueError("Serper rows missing expected 'link' field")

    parsed = [
        Result(
            url=str(r["link"]).strip(),
            title=str(r.get("title") or "").strip(),
            snippet=str(r.get("snippet") or "").strip(),
            source=SOURCE,
            engines=["serper"],
            index_origin=["google-serper"],
            meta={
                "provider": "serper",
                "position": r.get("position"),
                "date": str(r.get("date") or "").strip(),
            },
        )
        for r in valid_rows
    ]
    return _dedup_results(parsed, limit)


def _parse_tavily(body: str, *, limit: int = 25) -> list[Result]:
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(f"Tavily returned non-JSON: {e}") from e

    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise ValueError("Tavily payload missing expected 'results' list")

    rows = data["results"]
    if len(rows) == 0:
        return RecognizedEmpty("tavily: results=[]")

    valid_rows = [r for r in rows if isinstance(r, dict) and str(r.get("url") or "").strip()]
    if not valid_rows:
        raise ValueError("Tavily rows missing expected 'url' field")

    parsed = [
        Result(
            url=str(r["url"]).strip(),
            title=str(r.get("title") or "").strip(),
            snippet=str(r.get("content") or "").strip(),
            source=SOURCE,
            engines=["tavily"],
            index_origin=["tavily"],
            score=float(r.get("score") or 0.0),
            meta={
                "provider": "tavily",
                "published_date": str(r.get("published_date") or "").strip(),
            },
        )
        for r in valid_rows
    ]
    return _dedup_results(parsed, limit)


def _post_json(
    url: str,
    payload: dict[str, Any],
    *,
    source: str,
    query: str = "",
    headers: dict[str, str] | None = None,
    timeout: float = 45.0,
) -> tuple[str | None, Outcome | None]:
    """POST JSON with `core/http.py`-compatible blocker & 429 semantics."""
    try:
        r = httpx.post(
            url,
            json=payload,
            headers=headers or {},
            timeout=timeout,
            follow_redirects=True,
        )
    except httpx.TimeoutException as e:
        return None, RateLimited(
            query=query,
            source=source,
            retry_after_s=30,
            detail=f"timeout: {e}",
        )
    except httpx.HTTPError as e:
        return None, AccessBlocker(
            query=query,
            mechanism=Blocker.SERVER_ERROR,
            url=url,
            detail=str(e),
        )

    if r.status_code == 429:
        return None, RateLimited(
            query=query,
            source=source,
            retry_after_s=retry_after_seconds(r.headers.get("Retry-After")),
            detail="HTTP 429",
        )

    mech = detect_blocker(r.status_code, r.text, dict(r.headers))
    if mech:
        return None, AccessBlocker(
            query=query,
            mechanism=mech,
            url=url,
            detail=f"HTTP {r.status_code}",
        )
    return r.text, None


def _get_text(
    url: str,
    *,
    source: str,
    query: str = "",
    headers: dict[str, str] | None = None,
) -> tuple[str | None, Outcome | None]:
    """Delegate GETs through this module's `fetch` reference."""
    return fetch(url, source=source, query=query, headers=headers)


def search(
    query: str,
    categories: str = "general",
    pageno: int = 1,
    base: str | None = None,
    store: Store | None = None,
    limiter: Limiter | None = None,
    use_cache: bool = True,
    *,
    provider: str = "auto",
    limit: int = 25,
) -> Outcome:
    """Search the general web without requiring any background daemon.

    Uses keyless Mwmbl by default, commercial REST APIs when configured via env
    vars (`BRAVE_API_KEY`, `SERPER_API_KEY`, `TAVILY_API_KEY`), or delegates to
    SearXNG only when `--provider searxng`, `--base`, or `$SEARXNG_URL` is set.
    """
    try:
        prov = resolve_provider(provider, base=base)
    except ValueError as e:
        return AccessBlocker(
            query=query,
            coverage=Coverage(queried=[SOURCE], errored={SOURCE: "bad-provider"}),
            mechanism=Blocker.WRONG_ID_TYPE,
            url="",
            detail=str(e),
        )

    if prov == "searxng":
        from .searxng import search as _searxng_search

        return _searxng_search(
            query,
            categories=categories,
            pageno=pageno,
            base=base,
            store=store,
            limiter=limiter,
            use_cache=use_cache,
        )

    api_key = ""
    if prov in _KEY_ENV:
        env_var = _KEY_ENV[prov]
        api_key = os.environ.get(env_var, "").strip()
        if not api_key:
            return AccessBlocker(
                query=query,
                coverage=Coverage(queried=[SOURCE], errored={SOURCE: "missing-api-key"}),
                mechanism=Blocker.AUTH_WALL,
                url=_PROVIDER_URL[prov],
                detail=(
                    f"{env_var} is not set for provider {prov!r}; omit --provider "
                    f"for keyless Mwmbl or set {env_var}."
                ),
            )

    headers: dict[str, str] | None = None
    transport = _get_text
    index_origin = prov
    cap = max(1, int(limit))

    if prov == "mwmbl":
        url = f"{MWMBL_URL}?{urlencode({'s': query})}"
        headers = {"Accept": "application/json"}
        parse_fn = lambda body: _parse_mwmbl(  # noqa: E731
            body, limit=cap, verify_empty_fn=lambda: _verify_mwmbl_available(query)
        )
    elif prov == "brave":
        params: dict[str, Any] = {"q": query, "count": min(cap, 20)}
        if pageno > 1:
            params["offset"] = pageno - 1
        url = f"{BRAVE_URL}?{urlencode(params)}"
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        }
        parse_fn = lambda body: _parse_brave(body, limit=cap)  # noqa: E731
    elif prov == "serper":
        url = SERPER_URL
        payload: dict[str, Any] = {"q": query, "num": min(cap, 50)}
        if pageno > 1:
            payload["page"] = pageno
        if categories == "news":
            payload["type"] = "news"
        headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        index_origin = "google-serper"
        transport = lambda u, *, source, query="", headers=None: _post_json(  # noqa: E731
            u, payload, source=source, query=query, headers=headers
        )
        parse_fn = lambda body: _parse_serper(body, limit=cap)  # noqa: E731
    else:  # tavily
        url = TAVILY_URL
        payload = {
            "api_key": api_key,
            "query": query,
            "max_results": min(cap, 20),
        }
        if categories == "news":
            payload["topic"] = "news"
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        transport = lambda u, *, source, query="", headers=None: _post_json(  # noqa: E731
            u, payload, source=source, query=query, headers=headers
        )
        parse_fn = lambda body: _parse_tavily(body, limit=cap)  # noqa: E731

    prov_caveats = list(STANDING_CAVEATS) + [f"Active web provider: {prov}."]
    return run_source(
        query,
        source=SOURCE,
        url=url,
        searched=f"web ({prov}) for {query!r}",
        parse=parse_fn,
        store=store,
        limiter=limiter,
        use_cache=use_cache,
        index_origin=index_origin,
        cache_params={
            "provider": prov,
            "categories": categories,
            "pageno": pageno,
            "limit": cap,
        },
        headers=headers,
        transport=transport,
        exact_match_supported=False,
        corpus=CORPUS,
        not_searched=NOT_SEARCHED,
        caveats=prov_caveats,
    )
