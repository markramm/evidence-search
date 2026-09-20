"""HTTP with blocker detection.

Recognising *which* wall you hit is the difference between a retryable event, a
browser-escalation candidate, and a genuine absence.
"""
from __future__ import annotations

import re

import httpx

from .results import AccessBlocker, Blocker, RateLimited

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Signatures observed in production 2026-08-19.
# A framework mount point or bundle reference -- evidence the emptiness is a
# shell awaiting hydration, not simply a short page.
_SPA_SHELL = re.compile(
    r'id=["\'](root|app|__next|__nuxt|ember-basic-dropdown-wormhole)["\']'
    r'|<div[^>]+data-reactroot'
    r'|src=["\'][^"\']*(bundle|runtime|polyfills|main)[.-][^"\']*\.js',
    re.I)

# Challenge signatures must match the challenge being MOUNTED, not merely named.
#
# These once matched bare words anywhere in the first 8KB, which is where a
# site-wide <head> lives: a `<script src=".../recaptcha/api.js">` on a court
# search form made a 200 carrying full results report as AccessBlocker, and
# fetch() discards the body on a blocker, so the results were unrecoverable. A
# privacy policy naming its vendors did the same. So did the English noun
# "turnstile" -- transit records are in scope for this corpus.
#
# Same discipline as _SPA_SHELL below: require evidence of the mechanism, not a
# mention of it. Widget containers, the vendor's own challenge host, and the
# interstitial's own copy -- never the vendor name alone.
_SIGNATURES = [
    (re.compile(r'class=["\'][^"\']*cf-turnstile'
                r'|challenges\.cloudflare\.com'
                r'|data-sitekey=["\'][^"\']*["\'][^>]*turnstile', re.I), Blocker.TURNSTILE),
    (re.compile(r"cf-browser-verification|managed challenge|challenge-platform"
                r"|checking your browser before accessing", re.I), Blocker.CLOUDFLARE),
    (re.compile(r'class=["\'][^"\']*g-recaptcha'
                r'|www\.google\.com/recaptcha/api2/'
                r'|grecaptcha\.(?:render|execute|enterprise)', re.I), Blocker.RECAPTCHA),
    (re.compile(r'geo\.captcha-delivery\.com|captcha-delivery\.com/'
                r'|\bdatadome\.co\b|js\.datadome\.co|ct\.datadome\.co'
                r'|["\']?dd_cookie|window\.__dd|datadome[-_]?(?:captcha|challenge)', re.I),
     Blocker.DATADOME),
]

#: Text that survives tag-stripping. A challenge interstitial is mostly widget
#: and little prose; a served record page is the reverse. Cloudflare's "Sorry,
#: you have been blocked" boilerplate alone runs past 1200 chars, which the
#: first cut of this gate did not clear -- and an unrecognised wall is the worse
#: error, because the body then flows downstream as though it were content.
_CHALLENGE_PROSE_MAX = 3000

#: Below this much visible text, a challenge-shaped response carries no content.
#: Deliberately small: the question is "did we get a document or a stub", and a
#: real record page clears this by orders of magnitude. Anything larger starts
#: making judgements about whether a served page is long ENOUGH, which is a
#: different and much less defensible claim.
_SOFT_BLOCK_PROSE_MIN = 200


def _visible_len(body: str) -> int:
    """Length of the human-readable text, with tags and script bodies removed."""
    stripped = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", body)
    return len(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", stripped)).strip())


def detect_blocker(status: int, body: str, headers: dict | None = None) -> Blocker | None:
    # The signature scan is bounded, so measure prose over the SAME slice --
    # judging a page by its whole body while matching only its first 8KB made
    # large pages likelier to be skipped exactly where the scan is already blind.
    head = body[:8000]

    # A soft block: the wall answers with a SHAPE, not a signature.
    #
    # CourtListener turned on an AWS WAF JS-challenge that returns HTTP 202 with
    # a near-empty body and `x-amzn-waf-action: challenge`. It names no vendor in
    # the markup, so every _SIGNATURES pattern misses it, and 202 is a success
    # status -- so the body flowed downstream as content and `extract` printed
    # "the document was read, the terms are not in it" over a fetch that read
    # nothing. Reported 2026-08-28 and again 2026-09-17; the second reporter
    # noted the only tell was a `raw ~1 tok` line they happened to have a
    # successful `raw ~54,024 tok` fetch above in scrollback to compare against.
    # That is a false negative manufactured by the tool, which is the single
    # failure this package exists to prevent.
    #
    # Detection is on the pair (challenge-ish response, no content), never on
    # size alone: a legitimately tiny page is common and must stay a Hit.
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    waf_action = hdrs.get("x-amzn-waf-action", "")
    if waf_action:
        return Blocker.AWS_WAF
    if status == 202 and _visible_len(head) < _SOFT_BLOCK_PROSE_MIN:
        # 202 Accepted for a GET of a document is not a normal way to serve a
        # record; paired with no prose it is a challenge handoff.
        return Blocker.AWS_WAF

    # A challenge REPLACES the content. If the page also carries substantive
    # prose, the widget is furniture on a page that served us -- not a wall.
    challenged = None
    if _visible_len(head) <= _CHALLENGE_PROSE_MAX:
        for pattern, mech in _SIGNATURES:
            if pattern.search(head):
                challenged = mech
                break

    # A named mechanism outranks the status. 403 is the NORMAL status for a
    # Cloudflare wall, and only the named mechanisms set escalate_to_browser --
    # so falling through to FORBIDDEN reports a wall a headed browser could pass
    # as a flat refusal, which is the one thing this function exists to prevent.
    if challenged is not None:
        return challenged
    if status == 403:
        return Blocker.FORBIDDEN
    if status == 404:
        return Blocker.NOT_FOUND
    if status == 401:
        return Blocker.AUTH_WALL
    if status >= 500:
        return Blocker.SERVER_ERROR
    # A near-empty body with script tags and no text is an SPA shell.
    #
    # Thresholds are tuned against the shells observed 2026-08-19 (SAM.gov
    # entity detail, SBA DSBS): both were <2KB with a root div and bundle
    # tags. A false positive is not free -- JS_ONLY escalates to a browser,
    # which spends a Playwright launch -- so we additionally require a
    # mount-point or bundle signature rather than trusting size alone. A
    # legitimately small page (a 404 stub, a plain redirect notice) has
    # neither.
    if status == 200 and len(body.strip()) < 2000 and body.count("<script") >= 2 \
       and len(re.sub(r"<[^>]+>", "", body).strip()) < 200 \
       and _SPA_SHELL.search(body):
        return Blocker.JS_ONLY
    return None


#: Hosts whose HTML is Cloudflare-walled but which expose an unblocked machine
#: endpoint. Verified 2026-08-19; both halves matter, so neither is guessed.
#:
#: loc.gov: `/item/<id>/` HTML returns 403 while `/item/<id>/?fo=json` returns
#: 200 with the full catalog record. It is NOT a general bypass -- 
#: `/collections/...?fo=json` is blocked too -- so this is offered as a hint on
#: the blocked path, never as an automatic rewrite.
_JSON_ESCAPE_HATCH = {
    "www.loc.gov": ("loc.gov item pages are Cloudflare-walled, but the catalog "
                    "record is served unblocked at the same URL with `?fo=json` "
                    "(verified on /item/ paths; /collections/ is blocked either way)"),
    "loc.gov": ("loc.gov item pages are Cloudflare-walled, but the catalog record "
                "is served unblocked at the same URL with `?fo=json`"),
}


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
    # Headers are part of the evidence. The AWS WAF challenge announces itself
    # ONLY in `x-amzn-waf-action` -- the body it serves is empty and anonymous --
    # so discarding headers here is what let a soft block read as a document.
    mech = detect_blocker(r.status_code, text if not binary else "", dict(r.headers))
    if mech:
        # Name a known machine-readable route rather than letting a worker
        # rediscover it. One found the loc.gov JSON endpoint by hand after the
        # HTML 403'd, which is a good instinct and a wasted pass.
        from urllib.parse import urlsplit as _us
        hint = _JSON_ESCAPE_HATCH.get((_us(url).hostname or "").lower(), "")
        detail = f"HTTP {r.status_code}"
        if mech is Blocker.AWS_WAF:
            # The worker who hit this had the right instinct and still spent
            # several calls getting to it. Say the next move.
            detail += (" -- soft block: challenge response carried no document. "
                       "The session may be gated site-wide, not just this URL. "
                       "RETRY WITH --browser.")
        if hint and "fo=json" not in url:
            detail += f". TRY: {hint}"
        return None, AccessBlocker(query=query, mechanism=mech, url=url,
                                   detail=detail)
    return (r.content if binary else r.text), None
