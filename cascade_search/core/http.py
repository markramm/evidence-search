"""HTTP with blocker detection.

Recognising *which* wall you hit is the difference between a retryable event, a
browser-escalation candidate, and a genuine absence.
"""
from __future__ import annotations

import re

import httpx

from .results import AccessBlocker, Blocker, RateLimited, Coverage

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Signatures observed in production 2026-08-19.
_SIGNATURES = [
    (re.compile(r"turnstile", re.I), Blocker.TURNSTILE),
    (re.compile(r"cf-browser-verification|managed challenge|challenge-platform", re.I), Blocker.CLOUDFLARE),
    (re.compile(r"recaptcha|g-recaptcha", re.I), Blocker.RECAPTCHA),
    (re.compile(r"datadome", re.I), Blocker.DATADOME),
]


def detect_blocker(status: int, body: str) -> Blocker | None:
    head = body[:8000]
    for pattern, mech in _SIGNATURES:
        if pattern.search(head):
            return mech
    if status == 403:
        return Blocker.FORBIDDEN
    if status == 404:
        return Blocker.NOT_FOUND
    if status == 401:
        return Blocker.AUTH_WALL
    if status >= 500:
        return Blocker.SERVER_ERROR
    # A near-empty body with script tags and no text is an SPA shell.
    if status == 200 and len(body.strip()) < 2000 and body.count("<script") >= 2 \
       and len(re.sub(r"<[^>]+>", "", body).strip()) < 200:
        return Blocker.JS_ONLY
    return None


def fetch(url: str, *, source: str, query: str = "", timeout: float = 45.0,
          headers: dict | None = None, binary: bool = False):
    """Return (payload, outcome_or_None).

    On a block, payload is None and outcome is an AccessBlocker carrying the
    named mechanism and whether a browser could plausibly get past it.
    """
    hdrs = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
    hdrs.update(headers or {})
    try:
        r = httpx.get(url, headers=hdrs, timeout=timeout, follow_redirects=True)
    except httpx.TimeoutException as e:
        return None, RateLimited(query=query, source=source, retry_after_s=30,
                                 detail=f"timeout: {e}")
    except httpx.HTTPError as e:
        return None, AccessBlocker(query=query, mechanism=Blocker.SERVER_ERROR,
                                   url=url, detail=str(e))

    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        return None, RateLimited(query=query, source=source,
                                 retry_after_s=int(ra) if ra and ra.isdigit() else 60,
                                 detail="HTTP 429")

    text = "" if binary else r.text
    mech = detect_blocker(r.status_code, text if not binary else "")
    if mech:
        return None, AccessBlocker(query=query, mechanism=mech, url=url,
                                   detail=f"HTTP {r.status_code}")
    return (r.content if binary else r.text), None
