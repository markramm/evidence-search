"""SQLite-backed cache, rate limiter, and job queue.

All three share one database because all three are PROCESS-SHARED state. The
2026-08-19 session ran ~12 parallel workers, each of which assumed it owned the
whole search budget; they collectively exhausted 200 calls. Rate limits are a
property of the SOURCE, not of the worker, so the ledger must be shared.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

DEFAULT_DB = Path.home() / ".cascade-search" / "store.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY, source TEXT NOT NULL, payload TEXT NOT NULL,
    fetched_at REAL NOT NULL, expires_at REAL
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_source_at ON calls(source, at);
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
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not (k.lower().startswith(("utm_", "fbclid", "gclid", "mc_"))
                 or k.lower() in {"ref", "source", "_ga"})]
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
        """Store a response.

        NOTE (spec §5): callers MUST NOT cache payloads from engines whose terms
        forbid storage -- Brave's standard plans explicitly do. Those engines set
        cacheable=False and we record only the call, never the results.
        """
        if looks_degenerate(payload):
            raise CachePoisoned(
                f"refusing to cache {len(payload)} {source} rows that collapse to a "
                "single placeholder identity -- this is a parse failure, not a result set")
        now = time.time()
        self.conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
            (key, source, json.dumps(payload), now, now + ttl_s if ttl_s else None))

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
