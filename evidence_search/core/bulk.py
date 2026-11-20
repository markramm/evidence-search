"""Shared local bulk-dataset manager for Tier 3 bulk sources (`fec`, `sanctions`).

Issues #16 (FEC) and #3 (OFAC SDN + EU Consolidated sanctions) share the same
access shape: open, keyless bulk files (5-36 MB CSV/ZIP) that are downloaded
once per cycle or refresh window, parsed with a strict format-change threshold,
and indexed into a local SQLite database (`WAL` mode + `synchronous=NORMAL`)
for sub-second querying.

Centralising the download, rate-limit reservation, RFC 9110 Retry-After
handling, SHA-256 tracking, and `indexed_files` provenance table here prevents
each bulk source from re-implementing its own shell and drifting from the
absence invariant.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path

import httpx

from .http import retry_after_seconds
from .limits import Limiter
from .results import (
    AccessBlocker,
    Blocker,
    Coverage,
    Hit,
    Probe,
    RateLimited,
    Result,
    VerifiedAbsence,
    absence_meta,
    replay_cached,
    verified_absence,
)
from .store import CachePoisoned, Store, cache_key


def bulk_root(source: str, env_var: str | None = None) -> Path:
    """Resolve the local bulk cache directory for `source`."""
    if env_var and os.environ.get(env_var):
        return Path(os.environ[env_var])
    if os.environ.get("EVIDENCE_BULK_DIR"):
        return Path(os.environ["EVIDENCE_BULK_DIR"]) / source
    return Path.home() / ".evidence-search" / f"{source}_bulk"


def open_index_db(cache_dir: Path, schema: str) -> sqlite3.Connection:
    """Open `index.db` under `cache_dir` with WAL + NORMAL synchronous durability."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(cache_dir / "index.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(schema)
    return conn


def download_bulk_file(url: str, dest: Path, *, source: str, query: str,
                       timeout: float = 180.0, force: bool = False):
    """Stream `url` to `dest` atomically via a `.part` file.

    Returns (path, None) on success or (None, Outcome) on failure/block/429.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not force and dest.exists() and dest.stat().st_size > 0:
        return dest, None
    try:
        with httpx.stream("GET", url, timeout=timeout, follow_redirects=True,
                          headers={"User-Agent": "evidence-search"}) as r:
            if r.status_code == 404:
                return None, AccessBlocker(
                    query=query,
                    coverage=Coverage(queried=[source], errored={source: "not-found"}),
                    mechanism=Blocker.NOT_FOUND, url=url,
                    detail=f"no bulk file published at {url} (HTTP 404)")
            if r.status_code == 429:
                ra = retry_after_seconds(r.headers.get("Retry-After"))
                return None, RateLimited(
                    query=query,
                    coverage=Coverage(queried=[source], rate_limited=[source]),
                    source=source, retry_after_s=ra, detail="HTTP 429")
            if r.status_code >= 400:
                return None, AccessBlocker(
                    query=query,
                    coverage=Coverage(queried=[source], errored={source: "status"}),
                    mechanism=Blocker.SERVER_ERROR, url=url,
                    detail=f"HTTP {r.status_code}")
            tmp = dest.with_suffix(dest.suffix + ".part")
            with open(tmp, "wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
            tmp.rename(dest)
    except httpx.TimeoutException as e:
        return None, RateLimited(
            query=query,
            coverage=Coverage(queried=[source], rate_limited=[source]),
            source=source, retry_after_s=60, detail=f"timeout: {e}")
    except httpx.HTTPError as e:
        return None, AccessBlocker(
            query=query,
            coverage=Coverage(queried=[source], errored={source: "http"}),
            mechanism=Blocker.SERVER_ERROR, url=url, detail=str(e))
    return dest, None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def check_bulk_cache(
    query: str,
    *,
    source: str,
    searched: str,
    store: Store,
    use_cache: bool,
    index_origin: str = "own-index",
    cache_params: dict | None = None,
) -> Hit | VerifiedAbsence | None:
    """Return a cached Hit/VerifiedAbsence for a bulk SQLite query when present."""
    if not use_cache:
        return None
    key = cache_key(source, query, **(cache_params or {}))
    entry = store.get_entry(key, with_meta=True)
    if entry is None:
        return None
    return replay_cached(
        query,
        entry[0],
        entry[1],
        source=source,
        searched=searched,
        index_origin=index_origin,
        meta=entry[2],
    )


def finalize_bulk_query(
    query: str,
    *,
    source: str,
    endpoint: str,
    searched: str,
    results: list[Result],
    probe_params: dict,
    corpus: str,
    not_searched: list[str],
    caveats: list[str],
    store: Store,
    use_cache: bool = True,
    exact_match_supported: bool = True,
    index_origin: str = "own-index",
    cache_params: dict | None = None,
    ttl_s: int = 86400,
) -> Hit | VerifiedAbsence:
    """Build complete Probe + Coverage, write Store cache, and return Hit or VerifiedAbsence."""
    probe = Probe(
        source=source,
        endpoint=endpoint,
        query=query,
        params=probe_params,
        corpus=corpus,
        result_count=len(results),
        exact_match_supported=exact_match_supported,
        at=time.time(),
    )
    cov = Coverage(queried=[source], responsive=[source], indexes=[index_origin])
    key = cache_key(source, query, **(cache_params or {}))

    if not results:
        if use_cache:
            store.put(
                key,
                source,
                [],
                ttl_s=ttl_s,
                meta=absence_meta(
                    [probe],
                    not_searched,
                    caveats,
                    f"{source}: 0 local SQLite matches",
                ),
            )
        return verified_absence(
            query,
            cov,
            searched,
            probes=[probe],
            not_searched=list(not_searched),
            caveats=list(caveats),
        )

    if use_cache:
        try:
            store.put(key, source, [r.__dict__ for r in results], ttl_s=ttl_s)
        except CachePoisoned as e:
            cov.cache_write_refused = str(e)

    return Hit(query=query, coverage=cov, results=results, probes=[probe])
