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

    def put(self, key: str, source: str, payload, ttl_s: int | None = 86400) -> None:
        """Store a response.

        NOTE (spec §5): callers MUST NOT cache payloads from engines whose terms
        forbid storage -- Brave's standard plans explicitly do. Those engines set
        cacheable=False and we record only the call, never the results.
        """
        now = time.time()
        self.conn.execute(
            "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
            (key, source, json.dumps(payload), now, now + ttl_s if ttl_s else None))

    # ---- rate limiting -----------------------------------------------------
    def record_call(self, source: str) -> None:
        self.conn.execute("INSERT INTO calls (source, at) VALUES (?,?)", (source, time.time()))

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
