"""Typed outcomes — the heart of the tool.

The single most valuable output of a research pass is a DEFENSIBLE NEGATIVE.
An empty list cannot tell you whether you searched properly and found nothing,
or whether the door was shut in your face. This module makes that distinction
structural rather than a judgment call a tired worker makes at 2am.

See spec P2/P3 in cascade-research/notes/spec-cascade-search-federated-research-tool.md
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class Blocker(str, Enum):
    """Named access-blocking mechanisms.

    Naming the mechanism is what separates 'we were blocked' from 'it isn't there'.
    Every value here was observed in production on 2026-08-19.
    """
    CLOUDFLARE = "cloudflare-managed-challenge"   # Montana SOS
    TURNSTILE = "cloudflare-turnstile"            # OSCN, after ~10 fetches
    RECAPTCHA = "recaptcha"                       # Nebraska SOS
    DATADOME = "datadome"                         # Alaska interactive UI
    FORBIDDEN = "http-403"                        # dea.gov, justice.gov, courtlistener.com/recap
    AUTH_WALL = "auth-required"                   # CourtListener dockets endpoint
    JS_ONLY = "js-only-spa"                       # SAM.gov entity detail, SBA DSBS
    NOT_FOUND = "http-404"
    SERVER_ERROR = "http-5xx"


class GateType(str, Enum):
    """Human gates (spec P8, humanomation). Different gates, different handoffs."""
    CAPTCHA = "captcha"                # seconds
    LOGIN = "login"                    # minutes
    PAYMENT = "payment"                # minutes, needs authorization
    RECORDS_REQUEST = "records-request" # weeks
    PHONE_CALL = "phone-call"          # days


@dataclass
class Coverage:
    """What was actually asked, and who answered.

    Without this, '0 results' is uninterpretable. With it, '0 results across 5
    responsive engines spanning 3 distinct indexes' is a publishable finding.
    """
    queried: list[str] = field(default_factory=list)
    responsive: list[str] = field(default_factory=list)
    rate_limited: list[str] = field(default_factory=list)
    errored: dict[str, str] = field(default_factory=dict)
    indexes: list[str] = field(default_factory=list)  # distinct index_origin values
    cache_hits: int = 0
    cache_age_s: float | None = None   # age of a REPLAYED result, seconds
    elapsed_ms: int = 0
    #: Set when a payload was served but refused local caching. Deliberately
    #: NOT part of is_clean: a storage problem is not a retrieval problem.
    cache_write_refused: str | None = None
    #: Results dropped by a local exact-phrase filter, when the upstream engines
    #: ignored the caller's quotes. Reported so a thin result set is legible.
    exact_filtered: int = 0

    @property
    def is_clean(self) -> bool:
        """True only if every engine asked actually answered.

        A negative finding may ONLY be reported as a verified absence when this
        is True. Otherwise it is a tooling-limited negative and warrants retry.
        """
        return bool(self.queried) and not self.rate_limited and not self.errored

    def summary(self) -> str:
        parts = [f"{len(self.responsive)}/{len(self.queried)} responsive"]
        if self.indexes:
            parts.append(f"{len(set(self.indexes))} distinct index(es)")
        if self.rate_limited:
            parts.append(f"RATE-LIMITED: {', '.join(self.rate_limited)}")
        if self.errored:
            parts.append(f"ERRORED: {', '.join(self.errored)}")
        if self.cache_hits:
            age = ""
            if self.cache_age_s is not None:
                age = (f", {int(self.cache_age_s // 3600)}h old" if self.cache_age_s >= 3600
                       else f", {int(self.cache_age_s // 60)}m old" if self.cache_age_s >= 60
                       else ", fresh")
            parts.append(f"{self.cache_hits} cached{age}")
        return " | ".join(parts)


@dataclass
class Result:
    url: str
    title: str = ""
    snippet: str = ""
    source: str = ""
    engines: list[str] = field(default_factory=list)
    rank_by_engine: dict[str, int] = field(default_factory=dict)
    index_origin: list[str] = field(default_factory=list)
    score: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def unique_to_engine(self) -> bool:
        """Found by exactly one engine.

        On this beat these are often the MOST valuable results — obscure trade
        press, agency subpages, old forum posts. Naive rank fusion buries them;
        we surface them. This is the <person-h> failure mode (spec P4).
        """
        return len(self.engines) == 1


# ---- Outcomes ----------------------------------------------------------------

@dataclass
class Outcome:
    """Base. Never return a bare list; always return one of these."""
    query: str
    coverage: Coverage = field(default_factory=Coverage)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = type(self).__name__
        return d


@dataclass
class Hit(Outcome):
    results: list[Result] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.results)


@dataclass
class VerifiedAbsence(Outcome):
    """Searched the right corpus by the right method; it is not there.

    PUBLISHABLE. Only construct via `verified_absence()`, which enforces that
    coverage was clean.
    """
    searched: str = ""


@dataclass
class AccessBlocker(Outcome):
    """Blocked. NOT a negative finding. Name the mechanism."""
    mechanism: Blocker = Blocker.FORBIDDEN
    url: str = ""
    detail: str = ""
    escalate_to_browser: bool = False

    def __post_init__(self):
        # Mechanisms a real browser can plausibly get past (spec 3a).
        self.escalate_to_browser = self.mechanism in {
            Blocker.CLOUDFLARE, Blocker.TURNSTILE, Blocker.RECAPTCHA,
            Blocker.DATADOME, Blocker.JS_ONLY,
        }


@dataclass
class RateLimited(Outcome):
    """Budget or throttle exhausted. EXPLICITLY NOT content-exhausted.

    The corpus logged this failure mode three separate times before the tool
    existed; each cost a full dispatch cycle. Warrants retry, not a writeup.
    """
    source: str = ""
    retry_after_s: int | None = None
    detail: str = ""


@dataclass
class AwaitingHuman(Outcome):
    """Humanomation gate (spec P8).

    The pipeline automated to here. A human does the one thing only a human can
    do. Then the pipeline resumes. Instructions must be actionable in minutes —
    exact URL, exact query, exact field — never a prompt to re-derive the task.
    """
    gate_type: GateType = GateType.CAPTCHA
    instructions: str = ""
    url: str = ""
    resume_token: str = ""
    capture: list[str] = field(default_factory=list)  # what to bring back


def verified_absence(query: str, coverage: Coverage, searched: str):
    """Construct a VerifiedAbsence, or refuse.

    Guard rail: if any engine was rate-limited or errored, this is NOT a
    verified absence — it is a tooling-limited negative, and the corpus is
    emphatic that the two must never be conflated.
    """
    if not coverage.is_clean:
        return RateLimited(
            query=query, coverage=coverage, source=searched,
            detail=("Cannot certify absence: coverage incomplete "
                    f"({coverage.summary()}). Tooling-limited negative, NOT content-exhausted."),
        )
    return VerifiedAbsence(query=query, coverage=coverage, searched=searched)


def replay_cached(query: str, rows, fetched_at: float, *, source: str,
                  searched: str, index_origin: str = "n/a"):
    """Rebuild an outcome from cache, disclosing that it IS from cache.

    Every source previously synthesised a clean Coverage at read time, which
    asserted responsiveness that was never re-tested and dated the finding
    'now'. A replayed VerifiedAbsence is a claim about the world when the
    search ran -- so it carries that age, and says so in `searched`.
    """
    import time as _t
    age = max(0.0, _t.time() - fetched_at)
    cov = Coverage(queried=[source], responsive=[source], indexes=[index_origin],
                   cache_hits=1, cache_age_s=age)
    if rows:
        return Hit(query=query, coverage=cov, results=[Result(**r) for r in rows])
    mins = int(age // 60)
    when = f"{mins}m ago" if mins < 60 else f"{mins // 60}h ago"
    return verified_absence(query, cov, f"{searched} [cached, established {when}]")
