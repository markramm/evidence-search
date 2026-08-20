"""SQLite-backed cache, rate limiter, and job queue.

All three share one database because all three are PROCESS-SHARED state. The
2026-08-19 session ran ~12 parallel workers, each of which assumed it owned the
whole search budget; they collectively exhausted 200 calls. Rate limits are a
property of the SOURCE, not of the worker, so the ledger must be shared.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

#: The call ledger and cache. Override with CASCADE_DB.
#:
#: This is SHARED STATE by design -- the rate limiter's whole point is that
#: parallel workers spend one budget, not one each. That makes an override
#: necessary rather than merely convenient: a test suite (or a second profile)
#: must be able to get its own ledger instead of racing the real one.
DEFAULT_DB = Path(
    os.environ.get("CASCADE_DB")
    or Path.home() / ".cascade-search" / "store.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL,
    fetched_at REAL NOT NULL, expires_at REAL
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_source_at ON calls(source, at);
-- Full upstream records, verbatim.
--
-- The cache stores what we EMIT; this stores what we RECEIVED. Those diverged
-- badly on CourtListener, whose parser kept 4 of 30 fields: a worker counting an
-- industry lost `firm` and `attorney` (who retained the expert -- the money
-- question), `cause`, and `recap_documents`, and fell back to raw HTTP to get
-- fields we had already fetched and discarded.
--
-- Returning all 30 in every result is the opposite error: it spends the
-- caller's context on fields nobody asked for, which is what `extract` exists
-- to prevent. So: persist everything once, return a curated view, and let the
-- caller fetch the rest by id when a specific record turns out to matter.
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,   -- <source>:<stable upstream id or content hash>
    source TEXT NOT NULL,
    query TEXT NOT NULL,
    payload TEXT NOT NULL,        -- the upstream object, verbatim
    fetched_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_source_q ON records(source, query);
-- Consecutive failures per source, for escalating backoff.
--
-- Fixed windows assume every call has an equal chance of working. A source
-- that is hard-down does not: retrying it on schedule burns budget and wall
-- clock to learn what the last four calls already established. SearXNG models
-- this with continuous_errors driving ban_time_on_fail up to a ceiling; this
-- is the same idea over the shared ledger, so the backoff is shared too.
CREATE TABLE IF NOT EXISTS failures (
    source TEXT PRIMARY KEY,
    consecutive INTEGER NOT NULL,
    last_at REAL NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
    token TEXT PRIMARY KEY, source TEXT NOT NULL, state TEXT NOT NULL,
    payload TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
    result TEXT
);
"""


def normalize_url(url: str) -> str:
    """Normalize before hashing.

    SearXNG dedups on hash(result) with NO url normalization -- its own docs say
    so -- which lets the same page appear repeatedly under tracking params. Strip
    them, drop www., lowercase host, normalize the trailing slash.
    """
    try:
        p = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if p.port:
        host = f"{host}:{p.port}"
    # Strip ANALYTICS params only. `ref` and `source` were previously stripped
    # as tracking, but on government sites they are frequently SEMANTIC -- a
    # docket's source system, a registry's referring dataset -- and dropping
    # them collapses two distinct records into one in the dedup path.
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not (k.lower().startswith(("utm_", "fbclid", "gclid", "mc_"))
                 or k.lower() in {"_ga", "_gl", "igshid", "mkt_tok"})]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower() or "https", host, path, urlencode(q), ""))


def cache_key(source: str, query: str, **params) -> str:
    blob = json.dumps({"s": source, "q": query.strip().lower(), "p": params}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class CachePoisoned(Exception):
    """Refused to store a payload that looks like a parse failure.

    A transient parser bug becomes a 24-hour silent wrong answer once it is
    cached. On 2026-08-19 a ProPublica parse regression wrote 35 rows all
    titled "(unnamed appointee)" pointing at one fallback URL; every later
    call served them, looking like a healthy broad hit.
    """


# Placeholder titles a parser emits when it failed to find the real field.
_PLACEHOLDERS = ("(unnamed", "(untitled", "(unknown", "(none)", "n/a", "")


def looks_degenerate(rows) -> bool:
    """True if a multi-row result set collapses to one placeholder identity.

    Deliberately conservative: it fires only when EVERY row shares a single
    title AND that title reads as a parser fallback, or when every row shares
    one URL. Genuine result sets vary in at least one of those.
    """
    if not isinstance(rows, list) or len(rows) < 3:
        return False
    dicts = [r for r in rows if isinstance(r, dict)]
    if len(dicts) != len(rows):
        return False

    titles = {(r.get("title") or "").strip().lower() for r in dicts}
    if len(titles) == 1:
        only = next(iter(titles))
        if any(only.startswith(p) for p in _PLACEHOLDERS if p) or only == "":
            return True

    urls = {(r.get("url") or "").strip() for r in dicts}
    if len(urls) == 1 and len(titles) == 1:
        return True
    return False


class Store:
    def __init__(self, db_path: Path | None = None):
        self.path = Path(db_path or DEFAULT_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")  # concurrent workers
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        """Release the sqlite handle. Idempotent.

        Long-lived processes that open a Store per unit of work (the gate UI
        opens one per HTTP request) otherwise leak a connection each time.
        """
        conn = getattr(self, "conn", None)
        if conn is not None:
            conn.close()
            self.conn = None

    # ---- cache -------------------------------------------------------------
    def get(self, key: str):
        row = self.conn.execute(
            "SELECT payload, expires_at FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        payload, expires = row
        if expires and time.time() > expires:
            self.conn.execute("DELETE FROM cache WHERE key=?", (key,))
            return None
        return json.loads(payload)

    def get_entry(self, key: str):
        """Return (payload, fetched_at) or None.

        A cached VerifiedAbsence is a claim about the world at a point in time.
        Replaying it without that timestamp presents a stale negative as fresh.
        """
        row = self.conn.execute(
            "SELECT payload, expires_at, fetched_at FROM cache WHERE key=?", (key,)).fetchone()
        if not row:
            return None
        payload, expires, fetched_at = row
        if expires and time.time() > expires:
            self.conn.execute("DELETE FROM cache WHERE key=?", (key,))
            return None
        return json.loads(payload), fetched_at

    def put(self, key: str, source: str, payload, ttl_s: int | None = 86400) -> None:
        """Store a response, unless the source's policy forbids it.

        Spec §5: payloads from engines whose terms forbid storage must not be
        cached -- we record the call, never the results. That was documented
        here but enforced nowhere, so a future cacheable=False source would
        have been silently cached. No policy sets it today; the check exists so
        that adding one is sufficient.
        """
        from .limits import POLICIES
        pol = POLICIES.get(source)
        if pol is not None and not pol.cacheable:
            return
        if looks_degenerate(payload):
            raise CachePoisoned(
                f"refusing to cache {len(payload)} {source} rows that collapse to a "
                "single placeholder identity -- this is a parse failure, not a result set")
        now = time.time()
        self.conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
            (key, source, json.dumps(payload), now, now + ttl_s if ttl_s else None))

    # ---- failure backoff --------------------------------------------------
    def record_failure(self, source: str, reason: str = "") -> int:
        """Count a consecutive failure. Returns the new streak length."""
        row = self.conn.execute(
            "SELECT consecutive FROM failures WHERE source=?", (source,)).fetchone()
        n = (row[0] if row else 0) + 1
        self.conn.execute("INSERT OR REPLACE INTO failures VALUES (?,?,?,?)",
                          (source, n, time.time(), reason[:200]))
        return n

    def clear_failures(self, source: str) -> None:
        """A success resets the streak. Backoff punishes a RUN, not a blip."""
        self.conn.execute("DELETE FROM failures WHERE source=?", (source,))

    def failure_state(self, source: str):
        row = self.conn.execute(
            "SELECT consecutive, last_at, reason FROM failures WHERE source=?",
            (source,)).fetchone()
        if not row:
            return 0, 0.0, ""
        return row[0], row[1], row[2] or ""

    # ---- full upstream records ------------------------------------------
    def put_record(self, record_id: str, source: str, query: str, payload: dict) -> None:
        """Persist one upstream object verbatim, keyed for later retrieval."""
        self.conn.execute(
            "INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)",
            (record_id, source, query, json.dumps(payload), time.time()))

    def get_record(self, record_id: str):
        row = self.conn.execute(
            "SELECT payload, source, query, fetched_at FROM records WHERE record_id=?",
            (record_id,)).fetchone()
        if not row:
            return None
        return {"record_id": record_id, "source": row[1], "query": row[2],
                "fetched_at": row[3], "payload": json.loads(row[0])}

    def find_records(self, source: str | None = None, query: str | None = None,
                     limit: int = 50) -> list[dict]:
        sql = "SELECT record_id, source, query, fetched_at FROM records"
        where, args = [], []
        if source:
            where.append("source=?"); args.append(source)
        if query:
            where.append("query LIKE ?"); args.append(f"%{query}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY fetched_at DESC LIMIT ?"
        args.append(limit)
        return [{"record_id": r[0], "source": r[1], "query": r[2], "fetched_at": r[3]}
                for r in self.conn.execute(sql, args).fetchall()]

    # ---- rate limiting -----------------------------------------------------
    def record_call(self, source: str) -> None:
        self.conn.execute("INSERT INTO calls (source, at) VALUES (?,?)", (source, time.time()))

    def reserve_call(self, source: str, windows, min_interval_s: float = 0.0,
                     session_max: int | None = None, session_window_s: float = 3600.0):
        """Atomically claim one call against every window, or refuse.

        check-then-record is a TOCTOU race: N workers all read the same
        under-limit count before any of them writes, and all N proceed. That is
        precisely how the 2026-08-19 session burned 200 calls with ~12 workers.

        Here the claim is INSERTed first and validated inside a single
        BEGIN IMMEDIATE transaction, so SQLite serialises concurrent claimants.
        A claim that would breach a limit is rolled back, leaving no phantom
        call in the ledger.

        Returns (allowed, retry_after_s, reason).
        """
        now = time.time()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            return False, 1.0, f"{source}: ledger busy ({e})"
        try:
            # Spacing and the session cap are read BEFORE claiming: they are
            # properties of prior traffic, not of this claim.
            if min_interval_s:
                row = self.conn.execute(
                    "SELECT MAX(at) FROM calls WHERE source=?", (source,)).fetchone()
                last = row[0] if row and row[0] else None
                if last is not None and (gap := now - last) < min_interval_s:
                    self.conn.execute("ROLLBACK")
                    return False, round(min_interval_s - gap, 3), (
                        f"{source}: min interval {min_interval_s}s "
                        f"({round(min_interval_s - gap, 2)}s remaining)")

            if session_max is not None:
                used = self.conn.execute(
                    "SELECT COUNT(*) FROM calls WHERE source=? AND at > ?",
                    (source, now - session_window_s)).fetchone()[0]
                if used >= session_max:
                    self.conn.execute("ROLLBACK")
                    return False, None, (
                        f"session cap {session_max} reached for {source} "
                        f"in the last {int(session_window_s)}s")

            self.conn.execute("INSERT INTO calls (source, at) VALUES (?,?)", (source, now))

            # Validate AFTER claiming: the count now includes our own row, so
            # concurrent claimants cannot all see themselves as under the line.
            for window_s, max_calls in windows:
                n = self.conn.execute(
                    "SELECT COUNT(*) FROM calls WHERE source=? AND at > ?",
                    (source, now - window_s)).fetchone()[0]
                if n > max_calls:
                    oldest = self.conn.execute(
                        "SELECT MIN(at) FROM calls WHERE source=? AND at > ?",
                        (source, now - window_s)).fetchone()[0]
                    self.conn.execute("ROLLBACK")
                    retry = max(1.0, (oldest + window_s) - now) if oldest else window_s
                    return False, retry, (
                        f"{source}: {n - 1}/{max_calls} calls in {int(window_s)}s window")

            self.conn.execute("COMMIT")
            return True, None, ""
        except Exception:
            try:
                self.conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

    def count_calls(self, source: str, window_s: float) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM calls WHERE source=? AND at > ?",
            (source, time.time() - window_s)).fetchone()
        return row[0] if row else 0

    def oldest_call_in_window(self, source: str, window_s: float) -> float | None:
        row = self.conn.execute(
            "SELECT MIN(at) FROM calls WHERE source=? AND at > ?",
            (source, time.time() - window_s)).fetchone()
        return row[0] if row and row[0] else None

    # ---- jobs (incl. humanomation gates, spec P8) --------------------------
    def create_job(self, source: str, state: str, payload: dict) -> str:
        """Create a job, or return the token of an equivalent one already open.

        Gates are content-addressable in practice: same source, same URL, same
        thing for a human to do. Minting a fresh token per attempt meant one
        OSCN search parked three times under three tokens, and a person clearing
        the queue would have solved the identical Turnstile three times. Nine
        open gates covered five distinct URLs.

        Deduping here rather than at either call site covers both paths --
        `gates.open_gate` and `browser.fetch` -- which drifted independently.
        """
        url = (payload or {}).get("url")
        if url and state == "awaiting_human":
            row = self.conn.execute(
                "SELECT token FROM jobs WHERE source=? AND state=? "
                "AND json_extract(payload, '$.url')=? ORDER BY created_at LIMIT 1",
                (source, state, url)).fetchone()
            if row:
                # Refresh updated_at so the queue reflects renewed interest,
                # but keep the ORIGINAL token: anything already holding it stays
                # valid.
                self.conn.execute("UPDATE jobs SET updated_at=? WHERE token=?",
                                  (time.time(), row[0]))
                return row[0]

        token = uuid.uuid4().hex[:12]
        now = time.time()
        self.conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",
                          (token, source, state, json.dumps(payload), now, now, None))
        return token

    def set_job(self, token: str, state: str, result=None) -> None:
        self.conn.execute("UPDATE jobs SET state=?, updated_at=?, result=? WHERE token=?",
                          (state, time.time(), json.dumps(result) if result is not None else None, token))

    def get_job(self, token: str):
        row = self.conn.execute(
            "SELECT token, source, state, payload, created_at, updated_at, result "
            "FROM jobs WHERE token=?", (token,)).fetchone()
        if not row:
            return None
        return {"token": row[0], "source": row[1], "state": row[2],
                "payload": json.loads(row[3]), "created_at": row[4],
                "updated_at": row[5], "result": json.loads(row[6]) if row[6] else None}

    def list_jobs(self, state: str | None = None) -> list[dict]:
        if state:
            rows = self.conn.execute(
                "SELECT token FROM jobs WHERE state=? ORDER BY created_at", (state,)).fetchall()
        else:
            rows = self.conn.execute("SELECT token FROM jobs ORDER BY created_at").fetchall()
        return [self.get_job(r[0]) for r in rows]
