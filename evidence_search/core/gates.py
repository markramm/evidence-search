"""Humanomation: automate to the gate, hand off, resume, or cancel (spec P8).

The pattern Mark named from his Compound Thinking writing: the pipeline
automates up to a human gate, the human does only what a human must do, and the
pipeline automates again from there.

On 2026-08-19 this happened by hand. Five automated passes hit Montana's
Cloudflare wall; the conductor hand-wrote a ticket naming the entity number and
the exact field; Mark passed the gate in ~5 minutes; the conductor manually
OCR'd and filed the PDFs. It worked -- but every step of the handoff was bespoke.

This module makes it a protocol.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from .archive import archive
from .results import AwaitingHuman, GateType, Hit, Result, Coverage
from .store import Store

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]|sec|min|hr|day|days|hours|mins|secs)?\s*$", re.I)
_DURATION_UNITS = {
    "": 1.0, "s": 1.0, "sec": 1.0, "secs": 1.0,
    "m": 60.0, "min": 60.0, "mins": 60.0,
    "h": 3600.0, "hr": 3600.0, "hours": 3600.0,
    "d": 86400.0, "day": 86400.0, "days": 86400.0,
}


def parse_duration(spec: str) -> float:
    """Parse '30d', '24h', '15m', '90s' into seconds. Raises ValueError."""
    m = _DURATION_RE.match(str(spec or ""))
    if not m:
        raise ValueError(f"cannot read duration {spec!r} (use e.g. 30d, 24h, 15m, 60s)")
    val = float(m.group(1))
    unit = (m.group(2) or "").lower()
    return val * _DURATION_UNITS[unit]


def open_gate(store: Store, *, source: str, url: str, query: str,
              gate_type: GateType = GateType.CAPTCHA,
              capture: list[str] | None = None, instructions: str = "",
              requested_by: str | None = None) -> AwaitingHuman:
    """Park a job at a human gate and return the handoff."""
    req = (requested_by if requested_by is not None
           else os.environ.get("EVIDENCE_TASK") or "").strip()
    payload = {
        "url": url, "query": query, "gate_type": gate_type.value,
        "capture": capture or [], "instructions": instructions,
    }
    if req:
        payload["requested_by"] = req
    token = store.create_job(source, "awaiting_human", payload)
    return AwaitingHuman(
        query=query, coverage=Coverage(queried=[source], errored={source: "human-gate"}),
        gate_type=gate_type, url=url, resume_token=token, capture=capture or [],
        instructions=instructions or f"Open {url}, pass the gate, then:\n"
                                     f"  evidence-search gate resume {token} --file <saved>")


def list_gates(store: Store) -> list[dict]:
    return store.list_jobs("awaiting_human")


def cancel(store: Store, token: str | None = None, *,
           older_than_s: float | None = None, source: str | None = None,
           reason: str = "") -> tuple[list[str], str | None]:
    """Dismiss one gate by token (plus any duplicate of its URL) or bulk-cancel
    by age/source (#14).

    Returns (cancelled_tokens, error_or_None). The reason and timestamp are
    kept in `jobs.result` so the audit trail shows why the gate was cleared.
    """
    now = time.time()
    rec = {"cancelled_at": now, "reason": reason.strip() or "dismissed by operator"}

    if token:
        job = store.get_job(token)
        if not job:
            return [], f"no job with token {token!r}"
        if job["state"] != "awaiting_human":
            return [], f"job {token} is in state {job['state']!r}, not awaiting_human"
        url = (job.get("payload") or {}).get("url")
        cancelled = []
        for j in list_gates(store):
            if j["token"] == token or (url and (j.get("payload") or {}).get("url") == url
                                       and j.get("source") == job.get("source")):
                store.set_job(j["token"], "cancelled", rec)
                cancelled.append(j["token"])
        return cancelled, None

    if older_than_s is None and not source:
        return [], "specify a gate token, or pass --older-than / --source for bulk cancel"

    cancelled = []
    for j in list_gates(store):
        if source and j.get("source") != source:
            continue
        if older_than_s is not None and (now - j.get("created_at", now)) < older_than_s:
            continue
        store.set_job(j["token"], "cancelled", rec)
        cancelled.append(j["token"])
    return cancelled, None


def resume(store: Store, token: str, files: list[str] | None = None,
           do_archive: bool = True):
    """Resume a gated job with artifacts the human brought back.

    Archives each file with SHA-256, marks the job done, and returns a Hit
    carrying the archive records so downstream steps can proceed.
    """
    job = store.get_job(token)
    if not job:
        return None, f"no job with token {token!r}"
    if job["state"] != "awaiting_human":
        return None, f"job {token} is in state {job['state']!r}, not awaiting_human"

    records = []
    for f in files or []:
        p = Path(f).expanduser()
        if not p.exists():
            return None, f"file not found: {p}"
        rec = archive(p.read_bytes(), p.name, job["payload"].get("url", ""),
                      f"humanomation:gate:{token}") if do_archive else {"path": str(p)}
        rec["source_file"] = str(p)
        records.append(rec)

    store.set_job(token, "done", {"files": records, "resumed_at": time.time()})
    return Hit(query=job["payload"].get("query", ""),
               coverage=Coverage(queried=[job["source"]], responsive=[job["source"]]),
               results=[Result(url=job["payload"].get("url", ""),
                               title=f"resumed gate {token}", source=job["source"],
                               engines=[job["source"]],
                               meta={"archived": records, "job": job})]), None
