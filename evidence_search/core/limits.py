"""Per-source rate policy — ENCODED, not discovered.

Every limit here was either read from the source's own documentation or observed
in production. Discovering them at runtime costs a worker's dispatch cycle.
"""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

from .store import Store


@dataclass
class Policy:
    """Concurrent rolling windows, as (seconds, max_calls).

    CourtListener applies all three of its windows simultaneously and states that
    "the most restrictive one -- given your recent traffic -- is what controls".
    We model that directly: every window must pass.
    """
    windows: list[tuple[float, int]] = field(default_factory=list)
    min_interval_s: float = 0.0     # hard spacing between calls
    session_max: int | None = None  # e.g. OSCN Turnstile after ~10
    # A "session" is a rolling window in the SHARED ledger, not the lifetime of
    # one process. An in-memory counter cannot cap fan-out: 12 workers each get
    # their own, and the cap protects nothing.
    session_window_s: float = 3600.0
    #: Escalating backoff after consecutive failures: 2**(n-1) * base, capped.
    #: A source that is hard-down should not be retried on the same schedule as
    #: a healthy one -- that spends budget to relearn what the last four calls
    #: established.
    backoff_base_s: float = 15.0
    backoff_max_s: float = 900.0
    cacheable: bool = True          # False where terms forbid storing results
    index_origin: str = "n/a"       # own-crawl | bing | google | mixed | n/a
    note: str = ""


POLICIES: dict[str, Policy] = {
    # Docs (wiki.free.law, fetched 2026-08-19): 5/min, 50/hr, 125/day,
    # authenticated; windows apply concurrently; unauthenticated is stricter.
    "courtlistener": Policy(
        windows=[(60, 5), (3600, 50), (86400, 125)], min_interval_s=1.0,
        note="5/min, 50/hr, 125/day concurrent. Unauth stricter. "
             "storage.courtlistener.com serves PDFs where /recap 403s.",
    ),
    # Observed 2026-08-19: Cloudflare Turnstile engaged after ~10 fetches.
    "oscn": Policy(
        windows=[(3600, 30)], min_interval_s=2.0, session_max=8, session_window_s=1800,
        note="Turnstile after ~10 fetches/session. Session cap 8 (headroom) over a "
             "30min rolling window in the SHARED ledger, so fan-out cannot bypass it.",
    ),
    "usaspending": Policy(
        windows=[(60, 30)], min_interval_s=0.5,
        note="No key, no advertised limit (checked 2026-08-19: no rate headers). "
             "Paced conservatively anyway -- an unmetered public API is a courtesy, "
             "not a license. spending_by_award_count returns a REAL total.",
    ),
    "propublica_disclosures": Policy(
        windows=[(60, 15)], min_interval_s=1.0,
        note="SvelteKit __data.json endpoint; param is q= not search=. "
             "Undocumented internal API -- pace politely, expect shape changes.",
    ),
    # Crossref asks for a mailto in the UA (the "polite pool"); we send one.
    # A RESOLVER, not a counting source -- its search totals are meaningless.
    "crossref": Policy(
        windows=[(60, 40)], min_interval_s=0.3,
        note="No key. Polite-pool UA with mailto. DOI lookup is exact and "
             "authoritative; title search is FUZZY across ~150M records and its "
             "totals are NOT measurements.",
    ),
    "federal_register": Policy(
        windows=[(60, 30)], min_interval_s=0.4,
        note="No key, no advertised limit. `count` IS a real total -- the corpus "
             "is a defined body of government documents. 404 means zero results.",
    ),
    "docs": Policy(
        windows=[(60, 25)], min_interval_s=0.3,
        note="Documentation llms.txt/sitemap indexes. No key, no search engine.",
    ),
    # A local instance we operate: the constraint is the UPSTREAM engines it
    # proxies, not the container. Paced so a fan-out of workers cannot make
    # SearXNG hammer Google on our behalf and get the whole instance banned.
    "searxng": Policy(
        windows=[(60, 30)], min_interval_s=0.4, index_origin="mixed",
        note="Local container (SEARXNG_URL, default 127.0.0.1:8888). Limits protect "
             "the UPSTREAM engines it proxies. Needs `formats: [json]` in settings.yml.",
    ),
    "news_rss": Policy(windows=[(60, 20)], min_interval_s=0.5, index_origin="google"),
    "wayback": Policy(windows=[(60, 15)], min_interval_s=1.0, index_origin="own-crawl"),
}

DEFAULT_POLICY = Policy(windows=[(60, 20)], min_interval_s=0.5)


# --- Arbitrary URLs (`extract`, `browser`) -----------------------------------
#
# `extract <url>` and `browser <url>` fetched without reserving anything, so a
# fan-out of agents reading pages off one host was invisible to the ledger --
# including hosts whose limits the policies above exist to respect (OSCN's
# Turnstile counts every fetch from this address, whichever command sent it).
#
# Every such fetch now claims a slot in a BUCKET: the source that owns the
# host where there is one, otherwise a per-host bucket under HOST_POLICY.

HOST_PREFIX = "host:"

#: Politeness for a host with no policy of its own: at most one request a
#: second and 30 a minute, across every process on this machine. No failure
#: backoff -- a host that walls a plain fetch (403, Cloudflare) must keep
#: answering with that named blocker, and `--browser` must stay reachable; a
#: backoff would replace both with a RateLimited that says "retry".
HOST_POLICY = Policy(
    windows=[(60, 30)], min_interval_s=1.0, backoff_base_s=0.0,
    note="Per-host politeness for extract/browser fetches of arbitrary URLs.")

#: (host, path prefix, source). A host matches itself and its subdomains.
#:
#: CourtListener is scoped to /api/ on purpose: its 5/50/125 budget is the
#: API's. Pages and storage.courtlistener.com PDFs are not counted against it
#: upstream, and charging them here would spend a day's API budget on
#: downloads. They fall to per-host buckets instead.
SOURCE_HOSTS: list[tuple[str, str, str]] = [
    ("oscn.net", "", "oscn"),
    ("courtlistener.com", "/api/", "courtlistener"),
    ("api.usaspending.gov", "", "usaspending"),
    ("api.crossref.org", "", "crossref"),
    ("federalregister.gov", "", "federal_register"),
    ("projects.propublica.org", "", "propublica_disclosures"),
    ("news.google.com", "", "news_rss"),
    ("web.archive.org", "", "wayback"),
    ("archive.org", "", "wayback"),
]


def bucket_for_url(url: str) -> str:
    """The ledger name a fetch of `url` is charged to."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        parts, host = None, ""
    if not host:
        return HOST_PREFIX + "unknown"
    for owner, prefix, source in SOURCE_HOSTS:
        if (host == owner or host.endswith("." + owner)) and \
                (parts.path or "/").startswith(prefix or "/"):
            return source
    return HOST_PREFIX + host.removeprefix("www.")


# --- Account-specific overrides ---------------------------------------------
#
# The policies above encode what each source publishes for everyone. Some
# sources sell a higher tier: a CourtListener membership raises 5/50/125 to
# 20/250/1,000. Without a way to say so, the limiter refuses calls the account
# is entitled to, and a paid budget is spent at the free rate.
#
# An override replaces a source's budget WINDOWS and nothing else. Spacing,
# session caps and backoff stay as encoded: those protect the source from us,
# and no membership changes them.

LIMITS_ENV = "EVIDENCE_SEARCH_LIMITS"
LIMITS_FILE_ENV = "EVIDENCE_SEARCH_LIMITS_FILE"

_UNITS = {
    "s": 1, "sec": 1, "second": 1,
    "min": 60, "minute": 60,
    "h": 3600, "hr": 3600, "hour": 3600,
    "d": 86400, "day": 86400,
}
_WINDOW = re.compile(r"^(\d+)\s*/\s*(\d*)\s*([a-z]+)$")


class LimitsConfigError(ValueError):
    """An override that cannot be read as written.

    Raised, never skipped. A typo that fell back to the default would run a
    paid account at the free rate, or the reverse, with nothing to show for it.
    """


def _limits_file() -> Path:
    explicit = os.environ.get(LIMITS_FILE_ENV)
    return Path(explicit) if explicit else Path.home() / ".evidence-search" / "limits.conf"


def parse_limits(text: str, origin: str) -> dict[str, list[tuple[float, int]]]:
    """Parse `source = 20/min, 250/hr, 1000/day` lines (or `;`-separated)."""
    out: dict[str, list[tuple[float, int]]] = {}
    for raw in re.split(r"[\n;]", text):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" not in line:
            raise LimitsConfigError(f"{origin}: expected 'source = N/unit, ...', got {line!r}")
        source, spec = (part.strip() for part in line.split("=", 1))
        if source not in POLICIES:
            raise LimitsConfigError(
                f"{origin}: unknown source {source!r} (known: {', '.join(sorted(POLICIES))})")
        windows = []
        for item in spec.split(","):
            m = _WINDOW.match(item.strip().lower())
            unit = m.group(3) if m else ""
            if unit not in _UNITS and unit.endswith("s"):
                unit = unit[:-1]        # mins, hrs, days
            if not m or unit not in _UNITS or int(m.group(1)) < 1:
                raise LimitsConfigError(
                    f"{origin}: cannot read window {item.strip()!r} for {source} "
                    "(use e.g. 20/min, 250/hr, 1000/day)")
            windows.append((float(_UNITS[unit] * int(m.group(2) or 1)), int(m.group(1))))
        if not windows:
            raise LimitsConfigError(f"{origin}: no windows given for {source}")
        out[source] = sorted(windows)
    return out


def load_overrides() -> dict[str, tuple[list[tuple[float, int]], str]]:
    """{source: (windows, where it came from)}. The variable wins over the file.

    Read on every call: the file is a few bytes, and a long-lived process must
    not keep spending at a rate its operator has already changed.
    """
    found: dict[str, tuple[list[tuple[float, int]], str]] = {}
    path = _limits_file()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    for source, windows in parse_limits(text, str(path)).items():
        found[source] = (windows, str(path))
    for source, windows in parse_limits(os.environ.get(LIMITS_ENV, ""), LIMITS_ENV).items():
        found[source] = (windows, LIMITS_ENV)
    return found


def effective_policy(source: str) -> tuple[Policy, str | None]:
    """(policy in force, origin of its windows or None when built in)."""
    if source.startswith(HOST_PREFIX):
        return HOST_POLICY, None
    base = POLICIES.get(source, DEFAULT_POLICY)
    override = load_overrides().get(source)
    if override is None:
        return base, None
    windows, origin = override
    return replace(base, windows=list(windows)), origin


_HTTP_STATUS = re.compile(r"^HTTP (\d{3})\b")

#: Mechanisms that describe THIS REQUEST, not the health of the source.
_CALLER_SIDE = {"http-404", "auth-required", "http-unexpected-status",
                "wrong-identifier-type"}


def _detail_of(blocked) -> str:
    if isinstance(blocked, tuple):
        return str(blocked[1]) if len(blocked) > 1 else ""
    return str(getattr(blocked, "detail", "") or "")


def _mechanism_of(blocked) -> str:
    mech = getattr(blocked, "mechanism", None)
    if mech is None and isinstance(blocked, tuple) and blocked:
        mech = blocked[0]
    return str(getattr(mech, "value", mech) or "")


def upstream_retry_after(blocked) -> int | None:
    """Seconds the SOURCE told us to stay away, or None if this is not a 429.

    Two shapes reach here: a RateLimited whose detail starts "HTTP 429" (from
    core.http.fetch; its timeouts are RateLimited too, and are not this), and
    usaspending._post's ("rate", <Retry-After header>) tuple.
    """
    from .http import DEFAULT_RETRY_AFTER_S, retry_after_seconds
    if isinstance(blocked, tuple):
        return retry_after_seconds(blocked[1] if len(blocked) > 1 else "") \
            if blocked and blocked[0] == "rate" else None
    if type(blocked).__name__ == "RateLimited" and _detail_of(blocked).startswith("HTTP 429"):
        return int(getattr(blocked, "retry_after_s", None) or DEFAULT_RETRY_AFTER_S)
    return None


def is_callers_fault(blocked) -> bool:
    """True when the refusal is about OUR request rather than the source.

    A 400 for a malformed cursor, a 404 for a mistyped path, a 401 for a
    missing token: the source is up and answered. The failure streak is shared
    state, so counting these put a source into backoff for every other agent
    on the strength of one agent's bad request. 403 and 429 are excluded --
    those are the source pushing back -- and so is 408.
    """
    m = _HTTP_STATUS.match(_detail_of(blocked))
    if m:
        code = int(m.group(1))
        return 400 <= code < 500 and code not in (403, 408, 429)
    return _mechanism_of(blocked) in _CALLER_SIDE


class Limiter:
    """Rate discipline over the SHARED ledger.

    Prefer `reserve()`: it claims a call atomically. `check()` remains for
    read-only inspection (the `limits` command, pre-flight reporting), but a
    check() that is later followed by record() is a TOCTOU race and must not be
    used to gate a fetch.
    """

    #: Opt-in: block for short spacing waits (set by `--wait`). Never applies
    #: to budget windows -- sleeping out a 50/hr cap would hide a real limit
    #: behind a hang, which is exactly the confusion this tool exists to end.
    wait_for_spacing: bool = False
    MAX_SPACING_WAIT_S: float = 5.0

    #: Opt-in (`--wait-max SECONDS`): keep re-reserving, for at most this long,
    #: until a slot is actually claimed. Sleeps through spacing, a busy ledger,
    #: a shared 429 block, a failure backoff and the per-MINUTE window, each
    #: only when it ends inside the time left. An hour or day window, or a
    #: session cap, is never slept on: that returns at once with the time the
    #: window reopens.
    wait_max_s: float = 0.0
    #: Longest budget window `wait_max_s` will sleep out.
    SHORT_WINDOW_S: float = 60.0

    def __init__(self, store: Store):
        self.store = store

    def policy(self, source: str) -> Policy:
        return effective_policy(source)[0]

    def backoff_remaining(self, source: str) -> tuple[float, int, str]:
        """(seconds still to wait, streak length, reason). 0 when clear."""
        p = self.policy(source)
        n, last, reason = self.store.failure_state(source)
        if n < 2 or p.backoff_base_s <= 0:   # one failure is a blip; two is a pattern
            return 0.0, n, reason
        wait = min(p.backoff_max_s, p.backoff_base_s * (2 ** (n - 2)))
        remaining = (last + wait) - time.time()
        return (max(0.0, remaining), n, reason)

    def note_failure(self, source: str, reason: str = "") -> int:
        return self.store.record_failure(source, reason)

    def note_success(self, source: str) -> None:
        self.store.clear_failures(source)
        # Drops a 429 block that has run out. One still in force stays: see
        # Store.clear_block.
        self.store.clear_block(source)

    def note_outcome(self, source: str, blocked, count_failure: bool = True) -> None:
        """Feed the escalating-backoff counter from a fetch result.

        `reserve()` consults backoff_remaining() for EVERY source, but the
        counter it reads was fed from exactly one place -- run_source() -- so
        the four sources that predate that shell (crossref, federal_register,
        usaspending, searxng) never backed off no matter how many times they
        failed. Verified: five consecutive failures put a run_source source into
        a 120s backoff and left the others at zero.

        Three things can happen, and only the last feeds the streak:

        * an upstream 429 is written to the store as a block every process's
          reserve() honours (and is counted as a failure too);
        * a refusal that is about OUR request -- see is_callers_fault() -- is
          neither a failure nor a success, and changes nothing;
        * anything else is a genuine failure of the source.

        `count_failure=False` is for fetches of arbitrary URLs (`extract`,
        `browser`): a page that will not serve says little about the API that
        shares its host, so those share a 429 and nothing else.

        Bookkeeping must never convert a good answer into an error, so this
        swallows its own exceptions -- same posture as the call site in
        run_source, and the reason that one is wrapped there.
        """
        try:
            if blocked is None:
                self.note_success(source)
                return
            wait = upstream_retry_after(blocked)
            if wait is not None:
                self.store.block_until(source, time.time() + wait, "HTTP 429")
            if not count_failure or is_callers_fault(blocked):
                return
            # Sources hand back either an outcome object carrying `mechanism`
            # or, where the transport predates that shape (usaspending._post),
            # a plain ("rate"|"status"|"timeout"|..., detail) tuple. Keep the
            # specific reason in both cases -- backoff_remaining() surfaces it
            # to the operator, and "error" explains nothing. A RateLimited has
            # no mechanism; its detail ("HTTP 429", "timeout: ...") is the reason.
            reason = _mechanism_of(blocked) or _detail_of(blocked).split(":")[0].strip()
            self.note_failure(source, reason or "error")
        except Exception:
            pass

    def _attempt(self, source: str, p: Policy, honor_backoff: bool):
        """One non-sleeping try: (allowed, retry_after_s, reason, kind)."""
        # Refuse early when a source is in escalating backoff, before spending a
        # ledger slot on a call that has failed n times running.
        if honor_backoff:
            remaining, streak, reason = self.backoff_remaining(source)
            if remaining > 0:
                return False, round(remaining, 1), (
                    f"{source}: backing off after {streak} consecutive failures "
                    f"({reason[:60]}) -- {int(remaining)}s left"), "backoff"
        return self.store.reserve_call_ex(
            source, p.windows, min_interval_s=p.min_interval_s,
            session_max=p.session_max, session_window_s=p.session_window_s)

    def _worth_waiting(self, kind: str) -> bool:
        if kind.startswith("window:"):
            return float(kind.split(":", 1)[1]) <= self.SHORT_WINDOW_S
        return kind in ("busy", "spacing", "blocked", "backoff")

    def reserve(self, source: str, *, wait_s: float | None = None,
                honor_backoff: bool = True) -> tuple[bool, float | None, str]:
        """Atomically claim one call. (allowed, retry_after_s, reason).

        This is the gate every fetch must pass. By default it never sleeps and
        the caller decides. With a wait budget (`wait_max_s`, or `wait_s` for
        this call -- the larger applies) it re-reserves until a slot is claimed
        or the budget cannot cover the next wait, and then returns the refusal
        with its real retry-after.

        Waiters are not queued. Each sleeps until the slot it was refused
        should reopen, plus a random extra so that waiters refused together do
        not wake together, and then claims again through the same atomic
        ledger: whoever loses that claim is told the next reopening and
        sleeps again. No window can be exceeded, because nothing here grants a
        call -- only Store.reserve_call_ex does.

        `honor_backoff=False` skips the failure-streak check (never the 429
        block). It is for the browser retry of a fetch that was just walled:
        that retry is the remedy for the failure, not a repeat of it.
        """
        p = self.policy(source)
        budget = max(self.wait_max_s, wait_s or 0.0)
        deadline = time.monotonic() + budget
        spacing_retry_left = self.wait_for_spacing

        while True:
            allowed, retry, why, kind = self._attempt(source, p, honor_backoff)
            if allowed:
                return True, None, ""
            left = deadline - time.monotonic()
            if retry is not None and retry <= left and self._worth_waiting(kind):
                jitter = random.uniform(0.0, min(2.0, 0.1 + 0.25 * retry))
                time.sleep(min(left, retry + jitter))
                continue
            # `--wait` alone: retry once after a SHORT spacing wait.
            if (spacing_retry_left and kind == "spacing" and retry is not None
                    and retry <= self.MAX_SPACING_WAIT_S):
                spacing_retry_left = False
                time.sleep(retry)
                continue
            return False, retry, why

    def status(self, source: str) -> dict:
        """Everything that can refuse a call to `source`, read-only.

        For the `limits` command: an agent told "RateLimited" should be able to
        see WHICH limit, and when it lifts, without reading the ledger.
        """
        p, origin = effective_policy(source)
        now = time.time()
        usage = []
        for window_s, max_calls in p.windows:
            used = self.store.count_calls(source, window_s)
            oldest = self.store.oldest_call_in_window(source, window_s)
            usage.append({
                "window_s": window_s, "max": max_calls, "used": used,
                "reopens_in_s": (round(max(0.0, oldest + window_s - now), 1)
                                 if used >= max_calls and oldest else None)})
        session = None
        if p.session_max is not None:
            session = {"window_s": p.session_window_s, "max": p.session_max,
                       "used": self.store.count_calls(source, p.session_window_s)}
        until, why = self.store.blocked_until(source)
        remaining, streak, reason = self.backoff_remaining(source)
        refused = None
        if until:
            refused = f"upstream 429 block, {int(until - now) + 1}s left"
        elif remaining > 0:
            refused = f"failure backoff, {int(remaining) + 1}s left"
        elif session and session["used"] >= session["max"]:
            refused = f"session cap {session['max']} per {int(session['window_s'])}s reached"
        else:
            full = [u for u in usage if u["used"] >= u["max"]]
            if full:
                u = max(full, key=lambda u: u["reopens_in_s"] or 0)
                refused = (f"{u['used']}/{u['max']} in the {int(u['window_s'])}s window, "
                           f"reopens in {int(u['reopens_in_s'] or 0) + 1}s")
        return {
            "source": source,
            "usage": usage,
            "session": session,
            "min_interval_s": p.min_interval_s,
            "blocked_until": until or None,
            "blocked_for_s": round(until - now, 1) if until else None,
            "blocked_reason": why or None,
            "backoff": {"consecutive_failures": streak,
                        "remaining_s": round(remaining, 1),
                        "last_reason": reason or None},
            "next_call": refused or "allowed",
            "windows_from": origin or "built-in",
        }

    def check(self, source: str) -> tuple[bool, float | None, str]:
        """Read-only view of whether a call WOULD be allowed.

        Advisory only -- by the time you act on it another worker may have taken
        the slot. Gate real fetches with `reserve()`.
        """
        p = self.policy(source)

        if p.session_max is not None:
            used = self.store.count_calls(source, p.session_window_s)
            if used >= p.session_max:
                return False, None, (
                    f"session cap {p.session_max} reached for {source} "
                    f"in the last {int(p.session_window_s)}s")

        for window_s, max_calls in p.windows:
            n = self.store.count_calls(source, window_s)
            if n >= max_calls:
                oldest = self.store.oldest_call_in_window(source, window_s)
                retry = max(1.0, (oldest + window_s) - time.time()) if oldest else window_s
                return False, retry, (
                    f"{source}: {n}/{max_calls} calls in {int(window_s)}s window")

        if p.min_interval_s:
            recent = self.store.count_calls(source, p.min_interval_s)
            if recent:
                return False, p.min_interval_s, f"{source}: min interval {p.min_interval_s}s"
        return True, None, ""

    def record(self, source: str) -> None:
        """Record a call that was NOT claimed via reserve().

        Only for calls made outside the reservation path; reserve() already
        writes the ledger row.
        """
        self.store.record_call(source)
