"""Humanomation: automate to the gate, hand off, resume (spec P8).

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

import time
from pathlib import Path

from .archive import archive
from .results import AwaitingHuman, GateType, Hit, Result, Coverage
from .store import Store


def open_gate(store: Store, *, source: str, url: str, query: str,
              gate_type: GateType = GateType.CAPTCHA,
              capture: list[str] | None = None, instructions: str = "") -> AwaitingHuman:
    """Park a job at a human gate and return the handoff."""
    token = store.create_job(source, "awaiting_human", {
        "url": url, "query": query, "gate_type": gate_type.value,
        "capture": capture or [], "instructions": instructions,
    })
    return AwaitingHuman(
        query=query, coverage=Coverage(queried=[source], errored={source: "human-gate"}),
        gate_type=gate_type, url=url, resume_token=token, capture=capture or [],
        instructions=instructions or f"Open {url}, pass the gate, then:\n"
                                     f"  cascade-search gate resume {token} --file <saved>")


def list_gates(store: Store) -> list[dict]:
    return store.list_jobs("awaiting_human")


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
