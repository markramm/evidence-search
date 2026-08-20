"""Browser escalation — the answer to every wall we hit (spec 3a).

On 2026-08-19 every hard block was a JS/CAPTCHA gate: OSCN Turnstile, Montana
SOS Cloudflare Managed Challenge, Nebraska reCAPTCHA, Oklahoma resetTurnstile(),
SAM.gov and SBA DSBS as JS SPAs. Each became a hand-written MARK-ACTION ticket.

A real browser executes the JS, carries a session, and passes most of these. What
it cannot pass -- an interactive CAPTCHA a human must solve -- becomes an
AwaitingHuman gate rather than a dead end (spec P8, humanomation).

ETHICAL BOUNDARY, enforced by ALLOWED_HOSTS below: this is used ONLY to reach
PUBLIC RECORDS -- court dockets, state business registries, government datasets
that the agency itself publishes. Never a paywall, never an authentication
boundary, never personal data. Adding a host to that list is a deliberate act.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from .results import AccessBlocker, AwaitingHuman, Blocker, Coverage, GateType
from .store import Store

# Public-records hosts only. Extend deliberately, with a reason.
ALLOWED_HOSTS = {
    "oscn.net":              "Oklahoma State Courts Network -- public court dockets",
    "biz.sosmt.gov":         "Montana SOS -- public business registry",
    "sosmt.gov":             "Montana SOS",
    "www.nebraska.gov":      "Nebraska SOS -- public business registry",
    "sos.nebraska.gov":      "Nebraska SOS",
    "www.sos.ok.gov":        "Oklahoma SOS -- public business registry",
    "sam.gov":               "SAM.gov -- federal contractor registry",
    "dsbs.sba.gov":          "SBA Dynamic Small Business Search",
    "web.sba.gov":           "SBA",
    "www.courtlistener.com": "CourtListener -- public court records",
    "storage.courtlistener.com": "CourtListener document storage",
    "projects.propublica.org": "ProPublica public data projects",
}

# Signals that a human gate is on screen and no amount of waiting will clear it.
_HUMAN_GATE = re.compile(
    r"(verify you are human|i'm not a robot|select all images|"
    r"complete the security check|press and hold)", re.I)


def host_allowed(url: str) -> tuple[bool, str]:
    """Exact host, or a true subdomain of an allowed host.

    The boundary is the DOT: `oscn.net` permits `www.oscn.net` but must never
    permit `evil-oscn.net`. A bare `host.endswith(allowed)` gets that wrong --
    it reads the attacker-controlled label as if it were part of the allowed
    domain. Matching on "." + allowed is what makes the suffix a real label
    boundary rather than a string coincidence.
    """
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if not host:
        return False, ""
    if host in ALLOWED_HOSTS:
        return True, ALLOWED_HOSTS[host]
    for allowed, why in ALLOWED_HOSTS.items():
        if host.endswith("." + allowed):
            return True, why
    return False, ""


def fetch(url: str, *, source: str, query: str = "", wait_ms: int = 3500,
          headless: bool = True, wait_for: str | None = None,
          store: Store | None = None, timeout_ms: int = 45000):
    """Fetch a page with a real browser.

    Returns (html, outcome). On success outcome is None. If a human gate is on
    screen, outcome is AwaitingHuman carrying a resume token -- the pipeline
    automated to the gate and now needs a person (spec P8).
    """
    ok, why = host_allowed(url)
    if not ok:
        return None, AccessBlocker(
            query=query, coverage=Coverage(queried=[source], errored={source: "host-not-allowed"}),
            mechanism=Blocker.FORBIDDEN, url=url,
            detail=("Host is not on the public-records allow-list. Browser escalation is "
                    "restricted to government registries and court systems by design. "
                    "Add it to ALLOWED_HOSTS deliberately if it qualifies."))

    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        return None, AccessBlocker(
            query=query, coverage=Coverage(queried=[source], errored={source: "no-playwright"}),
            mechanism=Blocker.SERVER_ERROR, url=url,
            detail="playwright not installed: pip install playwright && playwright install chromium")

    t0 = time.time()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=headless)
            ctx = browser.new_context(
                user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
                viewport={"width": 1440, "height": 900}, locale="en-US")
            page = ctx.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=timeout_ms)
                except PWTimeout:
                    pass
            page.wait_for_timeout(wait_ms)   # let challenges self-resolve
            html = page.content()
            title = page.title()
            browser.close()
    except Exception as e:  # playwright raises a wide variety
        return None, AccessBlocker(
            query=query, coverage=Coverage(queried=[source], errored={source: "browser-error"}),
            mechanism=Blocker.SERVER_ERROR, url=url, detail=f"{type(e).__name__}: {e}")

    # Did a human-solvable gate survive the wait?
    if _HUMAN_GATE.search(html[:20000]) or "Turnstile" in title:
        token = ""
        if store:
            token = store.create_job(source, "awaiting_human", {
                "url": url, "query": query, "gate_type": GateType.CAPTCHA.value,
                "capture": ["page HTML after passing the challenge"],
            })
        return None, AwaitingHuman(
            query=query,
            coverage=Coverage(queried=[source], errored={source: "human-gate"},
                              elapsed_ms=int((time.time() - t0) * 1000)),
            gate_type=GateType.CAPTCHA, url=url, resume_token=token,
            capture=["page HTML after passing the challenge"],
            instructions=(
                f"A human challenge is on screen and the browser could not clear it.\n\n"
                f"  1. Open: {url}\n"
                f"  2. Pass the challenge.\n"
                f"  3. Save the page, or copy what you need.\n\n"
                f"Then: cascade-search gate resume {token} --file <saved.html>\n"
                f"(Or re-run with --headed to solve it in the browser this tool opens.)"))

    return html, None
