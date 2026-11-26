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

import os
import re
import time
from urllib.parse import urlsplit

from .results import AccessBlocker, AwaitingHuman, Blocker, Coverage, GateType
from .store import Store


class BrowserToolMissing(RuntimeError):
    """The local browser binary is not installed -- a tool failure, not a wall."""


# Public-records hosts only. Extend deliberately, with a reason.
#
# This dict is the SHIPPED baseline, not the whole list. It was the whole list
# for a month, during which agents filed 46 separate reports naming hosts they
# could not reach -- loc.gov (12 mentions), fec.gov (9), justice.gov (5),
# apps.occ.gov, scgov.net, county clerks -- because every addition required a
# code commit and a release. The error message told them to "add it to
# ALLOWED_HOSTS deliberately" and gave no route to do so. Meanwhile the two
# hosts that DID get added were never the ones requested.
#
# A hardcoded set is the right default and the wrong only option. See
# load_user_hosts(): operators extend it in a file, with the same requirement
# that has always applied -- a stated reason, public records only.
ALLOWED_HOSTS = {
    "oscn.net":              "Oklahoma State Courts Network -- public court dockets",
    "biz.sosmt.gov":         "Montana SOS -- public business registry",
    "sosmt.gov":             "Montana SOS",
    "www.nebraska.gov":      "Nebraska SOS -- public business registry",
    "sos.nebraska.gov":      "Nebraska SOS",
    "www.sos.ok.gov":        "Oklahoma SOS -- public business registry",
    "search.sunbiz.org":     "Florida Division of Corporations -- public business registry",
    "sunbiz.org":            "Florida Division of Corporations",
    "sosbes.sos.ky.gov":     "Kentucky SOS -- public business registry (ASP.NET postback)",
    "sos.ky.gov":            "Kentucky Secretary of State",
    "sam.gov":               "SAM.gov -- federal contractor registry",
    "dsbs.sba.gov":          "SBA Dynamic Small Business Search",
    "web.sba.gov":           "SBA",
    "www.courtlistener.com": "CourtListener -- public court records",
    "storage.courtlistener.com": "CourtListener document storage",
    "projects.propublica.org": "ProPublica public data projects",
    "schuylkillcountypa.gov": "Schuylkill County PA -- Recorder of Deeds / Assessment, public real-property records",
    "berkspa.gov":            "Berks County PA -- Assessment / property records (propertyrecords.berkspa.gov), public real-property records",
    # Checked 2026-11-26: report and decision pages render through this browser route;
    # direct fetch (extract) returns 403, and GAO's own search pages (bid-protest search,
    # /api/*) return Akamai "Access Denied" even here. Find a decision by web search
    # (site:gao.gov plus the B-number or GAO-number), then fetch its page with `browser`.
    "www.gao.gov":            "U.S. Government Accountability Office -- published reports and bid-protest decisions (legislative-branch public records). Pages render here; extract gets 403 and GAO's own search is blocked: find the page by web search (site:gao.gov B-number), then use browser",
}

# Signals that a human gate is on screen and no amount of waiting will clear it.
#
# "Verifying you are human" and the "Just a moment..." title are the Cloudflare
# interstitial's own wording. The first cut matched only "verify you are
# human", so that page came back from here as HTML, parsed to no rows, and was
# cached as a 24h absence.
_HUMAN_GATE = re.compile(
    r"(verify(?:ing)? (?:that )?you are (?:a )?human|i'm not a robot|select all images|"
    r"complete the security check|press and hold|"
    r"needs to review the security of your connection|"
    r"checking your browser before accessing|"
    r"<title>\s*just a moment)", re.I)
_GATE_TITLE = re.compile(r"turnstile|just a moment|attention required", re.I)


#: Optional CDP endpoint (e.g. Obscura or a local CDP server) for rendering
#: ungated JS-only pages without launching a full Chromium instance (#26).
#: Explicit non-goal: never used for anti-detection or bot-wall bypass.
CDP_ENV = "EVIDENCE_BROWSER_CDP"


def current_backend(headless: bool = True) -> str:
    """Name the render backend in force: 'cdp:<url>' or 'chromium'."""
    cdp = (os.environ.get(CDP_ENV) or "").strip()
    if cdp and headless:
        return f"cdp:{cdp}"
    return "chromium"


#: Operator-supplied additions: {host: reason}. JSON, at $EVIDENCE_ALLOWED_HOSTS
#: or ~/.evidence-search/allowed_hosts.json.
#:
#: A reason is REQUIRED, and it is not paperwork. The allow-list is the tool's
#: ethical boundary; CONTRIBUTING calls adding a host "a deliberate act". An
#: entry with no stated reason is refused rather than silently trusted, so the
#: file cannot decay into an undocumented pile of hostnames.
def load_user_hosts(path=None) -> dict[str, str]:
    import json
    from pathlib import Path

    p = path or os.environ.get("EVIDENCE_ALLOWED_HOSTS")
    if p:
        p = Path(p)
    else:
        p = Path.home() / ".evidence-search" / "allowed_hosts.json"
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        # A broken config must not silently widen or narrow the boundary.
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for host, why in raw.items():
        if isinstance(host, str) and isinstance(why, str) and why.strip():
            out[host.lower().strip().rstrip(".")] = why.strip()
    return out


def effective_hosts(path=None) -> dict[str, str]:
    """Shipped baseline plus operator additions. Never fewer than the baseline."""
    return {**ALLOWED_HOSTS, **load_user_hosts(path)}


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
    hosts = effective_hosts()
    if host in hosts:
        return True, hosts[host]
    for allowed, why in hosts.items():
        if host.endswith("." + allowed):
            return True, why
    return False, ""


def fetch(url: str, *, source: str, query: str = "", wait_ms: int = 3500,
          headless: bool = True, wait_for: str | None = None,
          store: Store | None = None, timeout_ms: int = 45000,
          allow_ungated_js: bool = False):
    """Fetch a page with a real browser (or an optional CDP render backend).

    Returns (html, outcome). On success outcome is None. If a human gate is on
    screen, outcome is AwaitingHuman carrying a resume token -- the pipeline
    automated to the gate and now needs a person (spec P8).

    `allow_ungated_js=True` is ONLY for rendering an ungated JS-only SPA shell
    through an explicitly configured CDP render backend (`EVIDENCE_BROWSER_CDP`,
    issue #26) when `extract` hit a 200 status with an empty DOM. Without
    `allow_ungated_js` (or without a CDP backend), `ALLOWED_HOSTS` governs every
    call. Even with `allow_ungated_js`, any page that turns out to carry a human
    challenge is refused unless the host is on `ALLOWED_HOSTS`.
    """
    ok, why = host_allowed(url)
    cdp_url = (os.environ.get(CDP_ENV) or "").strip() if headless else ""
    if not ok and not (allow_ungated_js and cdp_url):
        return None, AccessBlocker(
            query=query, coverage=Coverage(queried=[source], errored={source: "host-not-allowed"}),
            mechanism=Blocker.FORBIDDEN, url=url,
            detail=(f"Host {(urlsplit(url).hostname or '?').lower()} is not on the "
                    "public-records allow-list. Browser escalation is restricted to "
                    "government registries and court systems by design.\n"
                    "If it qualifies (public records only -- never a paywall, an auth "
                    "boundary, or personal data), add it with a reason:\n"
                    '  echo \'{"%s": "why this is a public record"}\' '
                    "> ~/.evidence-search/allowed_hosts.json\n"
                    "Run `evidence-search hosts` to see what is currently allowed."
                    % (urlsplit(url).hostname or "host").lower()))

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
            if cdp_url:
                browser = pw.chromium.connect_over_cdp(cdp_url)
                contexts = getattr(browser, "contexts", None) or []
                if contexts:
                    ctx = contexts[0]
                else:
                    ctx = browser.new_context()
                page = ctx.new_page()
            else:
                browser = pw.chromium.launch(headless=headless)
                ctx = browser.new_context(
                    user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"),
                    viewport={"width": 1440, "height": 900}, locale="en-US")
                page = ctx.new_page()
            try:
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                if wait_for:
                    try:
                        page.wait_for_selector(wait_for, timeout=timeout_ms)
                    except PWTimeout:
                        pass
                page.wait_for_timeout(wait_ms)   # let challenges self-resolve
                html = page.content()
                title = page.title()
            finally:
                if cdp_url:
                    try:
                        page.close()
                    except Exception:
                        pass
                browser.close()
    except Exception as e:  # playwright raises a wide variety
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            raise BrowserToolMissing(
                f"Playwright browser binary is missing ({msg.splitlines()[0]}). "
                "Run `playwright install chromium`."
            ) from e
        if cdp_url and ("ECONNREFUSED" in msg or "connect_over_cdp: WebSocket error" in msg):
            raise BrowserToolMissing(
                f"CDP render backend at {cdp_url} is unreachable ({msg.splitlines()[0]}). "
                f"Start it with `deploy/obscura.sh up` or unset {CDP_ENV}."
            ) from e
        return None, AccessBlocker(
            query=query, coverage=Coverage(queried=[source], errored={source: "browser-error"}),
            mechanism=Blocker.SERVER_ERROR, url=url, detail=f"{type(e).__name__}: {e}")

    # Did a human-solvable gate survive the wait?
    if _HUMAN_GATE.search(html[:20000]) or _GATE_TITLE.search(title or ""):
        if not ok:
            # An ungated-JS render attempt hit a challenge on a non-allow-listed
            # host: never park a human gate outside ALLOWED_HOSTS.
            return None, AccessBlocker(
                query=query,
                coverage=Coverage(queried=[source], errored={source: "challenge-on-unallowed-host"}),
                mechanism=Blocker.TURNSTILE, url=url,
                detail="JS render backend encountered a human challenge on a host not in ALLOWED_HOSTS.")
        token = ""
        req_by = (os.environ.get("EVIDENCE_TASK") or "").strip()
        if store:
            payload = {
                "url": url, "query": query, "gate_type": GateType.CAPTCHA.value,
                "capture": ["page HTML after passing the challenge"],
            }
            if req_by:
                payload["requested_by"] = req_by
            token = store.create_job(source, "awaiting_human", payload)
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
                f"Then: evidence-search gate resume {token} --file <saved.html>\n"
                f"(Or re-run with --headed to solve it in the browser this tool opens.)"))

    return html, None
