"""Typed outcomes — the heart of the tool.

The single most valuable output of a research pass is a DEFENSIBLE NEGATIVE.
An empty list cannot tell you whether you searched properly and found nothing,
or whether the door was shut in your face. This module makes that distinction
structural rather than a judgment call a tired worker makes at 2am.

Consumers branch on outcome type, never on emptiness.
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
    AWS_WAF = "aws-waf-challenge"                 # CourtListener, site-wide from 2026-08-28
    NOT_FOUND = "http-404"
    SERVER_ERROR = "http-5xx"
    WRONG_ID_TYPE = "wrong-identifier-type"       # caller-side: right corpus, wrong key


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
class Probe:
    """One endpoint asked one exact question, and got nothing back.

    A negative is only as good as the record of what was actually asked. Prose
    like "searched OSCN" cannot be audited, re-run, or challenged; this can.
    """
    source: str                      # e.g. "courtlistener"
    endpoint: str = ""               # the URL or API path actually hit
    query: str = ""                  # the EXACT query string sent
    params: dict[str, Any] = field(default_factory=dict)   # filters that scoped it
    corpus: str = ""                 # what that endpoint covers, in one line
    result_count: int = 0
    exact_match_supported: bool | None = None   # None = unknown, see below
    at: float = 0.0                  # unix time of the probe

    def describe(self) -> str:
        bits = [f"{self.source}: {self.query!r} -> {self.result_count} results"]
        if self.params:
            bits.append("(" + ", ".join(f"{k}={v}" for k, v in self.params.items()) + ")")
        if self.exact_match_supported is False:
            bits.append("[FUZZY: endpoint does not honour exact-phrase matching]")
        return " ".join(bits)


@dataclass
class VerifiedAbsence(Outcome):
    """Searched the right corpora by the right methods; it is not there.

    PUBLISHABLE -- but publishable claims about absence need to state their own
    limits, because proving a negative in absolute terms is usually impossible.
    What is actually provable is narrower and worth saying precisely: THESE
    endpoints, asked THESE exact questions, returned nothing at THIS time.

    So the record is structured rather than prose. `probes` carries one entry
    per endpoint actually queried, with the exact query and the filters that
    scoped it, which makes the negative auditable, reproducible, and arguable.
    A reader can see what was NOT asked as easily as what was.

    `searched` remains as the human-readable summary, derived from the probes.
    """
    searched: str = ""
    probes: list[Probe] = field(default_factory=list)
    #: Corpora a reader should know were NOT consulted -- the honest boundary of
    #: the claim. A state-court silence is not a federal-court silence.
    not_searched: list[str] = field(default_factory=list)
    #: Why this negative might still be wrong. Populated by the source when it
    #: knows its own blind spots (fuzzy matching, page caps, coverage gaps).
    caveats: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Refuse to exist on dirty coverage.

        This is the one invariant the tool is named for, and it was previously
        guarded only by verified_absence() -- so the obvious constructor
        bypassed it in silence, producing an object that renders as publishable
        and exits 11 while resting on engines that never answered. Convention is
        too thin for the claim this type makes.

        Callers should use verified_absence(), which downgrades to RateLimited
        rather than raising. Reaching the constructor directly with incomplete
        coverage is a programming error, so it is loud.
        """
        if not self.coverage.is_clean:
            raise ValueError(
                "VerifiedAbsence requires clean coverage; got "
                f"{self.coverage.summary()}. A tooling-limited negative is not "
                "an absence -- use verified_absence(), which downgrades to "
                "RateLimited."
            )

    @property
    def is_absolute(self) -> bool:
        """True only if every probe was exact-matched and nothing was left unsearched.

        Almost never true, and that is the point: the property exists so callers
        stop treating a bounded negative as an unbounded one.
        """
        return (bool(self.probes)
                and all(p.exact_match_supported for p in self.probes)
                and not self.not_searched
                and not self.caveats)

    def claim(self) -> str:
        """The sentence a reporter may actually publish."""
        n = len(self.probes)
        head = (f"Not found in {n} searched corpus/corpora"
                if n else "Not found")
        if self.not_searched:
            head += f"; NOT searched: {', '.join(self.not_searched)}"
        if self.caveats:
            head += f". Caveats: {'; '.join(self.caveats)}"
        return head

    def to_dict(self) -> dict:
        """Lift the exact strings asked to the TOP LEVEL.

        They were always present, nested one level down inside probes[].query.
        Reaching them meant walking the structure -- the same defect the
        total_matches fix addressed on Hit. A consumer deciding whether to
        trust a negative should not have to dig for the thing the negative is
        ABOUT. `asked` is the specification; `outcome` is the verdict on it.
        """
        d = super().to_dict()
        seen: list[dict] = []
        for pr in self.probes:
            q = (pr.query or "").strip()
            if not q:
                continue
            entry = {
                "query": q,
                "source": pr.source,
                "params": {k: v for k, v in (pr.params or {}).items() if v not in (None, "")},
                "exact_match_supported": pr.exact_match_supported,
            }
            if entry not in seen:
                seen.append(entry)
        d["asked"] = seen
        d["asked_note"] = ("An absence of THESE STRINGS. A variant spelling, married "
                           "name, transliteration, or data-entry variant is a "
                           "different question and was not asked.")
        return d

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
            Blocker.DATADOME, Blocker.JS_ONLY, Blocker.AWS_WAF,
        }

    @property
    def is_wall(self) -> bool:
        """True when something actively refused us; False when the URL is wrong.

        A 404 is not a wall. Two field workers read `AccessBlocker: http-404`
        as suppression: one nearly wrote that a trade publication was blocking
        its own about-page (the path was simply `/about-us`), which on this beat
        is a spicy and completely false claim; another saw it ~1 in 7 while
        sweeping a sequential-id range, where a missing id is ordinary and is
        itself useful signal. Both paused to re-check a healthy tool. The
        mechanism was always in the output -- the REGISTER was what misled, so
        the split is at render time, not in classification.
        """
        return self.mechanism not in {Blocker.NOT_FOUND}


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


def verified_absence(query: str, coverage: Coverage, searched: str,
                     probes: "list[Probe] | None" = None,
                     not_searched: list[str] | None = None,
                     caveats: list[str] | None = None):
    """Construct a VerifiedAbsence, or refuse.

    Guard rail: if any engine was rate-limited or errored, this is NOT a
    verified absence -- it is a tooling-limited negative, and the corpus is
    emphatic that the two must never be conflated.

    `probes` records what was ACTUALLY asked, endpoint by endpoint, with the
    exact query. Proving a negative in absolute terms is usually impossible;
    what IS provable is "these endpoints, asked these questions, returned
    nothing at this time." A caller that passes probes gets an auditable
    negative; one that passes none gets a prose-only claim, which is weaker and
    now visibly so.
    """
    if not coverage.is_clean:
        return RateLimited(
            query=query, coverage=coverage, source=searched,
            detail=("Cannot certify absence: coverage incomplete "
                    f"({coverage.summary()}). Tooling-limited negative, NOT content-exhausted."),
        )
    return VerifiedAbsence(query=query, coverage=coverage, searched=searched,
                           probes=probes or [], not_searched=not_searched or [],
                           caveats=caveats or [])


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
