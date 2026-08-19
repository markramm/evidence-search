"""Per-source rate policy — ENCODED, not discovered.

Every limit here was either read from the source's own documentation or observed
in production. Discovering them at runtime costs a worker's dispatch cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
        note="No documented limit found; pacing conservatively.",
    ),
    "propublica_disclosures": Policy(
        windows=[(60, 15)], min_interval_s=1.0,
        note="SvelteKit __data.json endpoint; param is q= not search=. "
             "Undocumented internal API -- pace politely, expect shape changes.",
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
    # Terms: standard plans do NOT grant storage rights (brave.com/search/api).
    "brave": Policy(
        windows=[(1, 20)], cacheable=False, index_origin="own-crawl",
        note="STORAGE RIGHTS REQUIRED to cache results. cacheable=False unless "
             "operator asserts a storage-rights plan in config.",
    ),
    "marginalia": Policy(
        windows=[(60, 10)], min_interval_s=2.0, index_origin="own-crawl",
        note="Public key shares one global rate limit; HTTP 503 when hit. "
             "Free non-commercial key is CC-BY-NC-SA 4.0 -- attribution required.",
    ),
}

DEFAULT_POLICY = Policy(windows=[(60, 20)], min_interval_s=0.5)


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

    def __init__(self, store: Store):
        self.store = store

    def policy(self, source: str) -> Policy:
        return POLICIES.get(source, DEFAULT_POLICY)

    def reserve(self, source: str) -> tuple[bool, float | None, str]:
        """Atomically claim one call. (allowed, retry_after_s, reason).

        This is the gate every fetch must pass. Never sleeps; the caller decides.
        """
        p = self.policy(source)
        allowed, retry, why = self.store.reserve_call(
            source, p.windows, min_interval_s=p.min_interval_s,
            session_max=p.session_max, session_window_s=p.session_window_s)

        # Retry once after a SHORT spacing wait, when the caller asked for it.
        if (not allowed and self.wait_for_spacing and "min interval" in why
                and retry is not None and retry <= self.MAX_SPACING_WAIT_S):
            import time as _t
            _t.sleep(retry)
            allowed, retry, why = self.store.reserve_call(
                source, p.windows, min_interval_s=p.min_interval_s,
                session_max=p.session_max, session_window_s=p.session_window_s)
        return allowed, retry, why

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
                import time as _t
                retry = max(1.0, (oldest + window_s) - _t.time()) if oldest else window_s
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
