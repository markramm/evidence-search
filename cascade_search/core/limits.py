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
        windows=[(3600, 30)], min_interval_s=2.0, session_max=8,
        note="Turnstile after ~10 fetches/session. Session cap set to 8 for headroom.",
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
    def __init__(self, store: Store):
        self.store = store
        self._session_counts: dict[str, int] = {}

    def policy(self, source: str) -> Policy:
        return POLICIES.get(source, DEFAULT_POLICY)

    def check(self, source: str) -> tuple[bool, float | None, str]:
        """(allowed, retry_after_s, reason). Never sleeps; the caller decides."""
        p = self.policy(source)

        if p.session_max is not None and self._session_counts.get(source, 0) >= p.session_max:
            return False, None, (
                f"session cap {p.session_max} reached for {source} "
                f"({p.note or 'per-session limit'})")

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
        self.store.record_call(source)
        self._session_counts[source] = self._session_counts.get(source, 0) + 1
