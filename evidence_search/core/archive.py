"""Archive on retrieval — never as an afterthought (spec P5).

Workers currently hash and file by hand, and sometimes skip it: on 2026-08-19 a
worker read three court opinions via API/OCR and archived none of them, caught
only at QC. If the tool fetches it, the tool archives it.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from .store import _resolve

#: Where retrieved documents land. Override with EVIDENCE_ARCHIVE
#: (CASCADE_ARCHIVE still works).
#:
#: This used to be an unconditional absolute path into one operator's KB, which
#: made the tool unusable by anyone else and -- worse -- silently wrote into a
#: different KB than the install instructions implied.
#:
#: The legacy ~/.cascade-search location is honoured when it is the only one
#: holding an archive; see store._resolve. Splitting the archive across two
#: roots would put a seam in an append-only provenance chain.
DEFAULT_ARCHIVE = _resolve("EVIDENCE_ARCHIVE", "CASCADE_ARCHIVE", "archive")
MANIFEST = "MANIFEST-evidence-search.txt"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive(data: bytes, filename: str, url: str, method: str,
            archive_dir: Path | None = None) -> dict:
    """Write bytes, hash them, append a manifest line. Idempotent by content.

    Returns {path, sha256, bytes, existing} -- `existing` True if identical
    content was already archived under that name, so re-fetching is cheap and
    does not duplicate.
    """
    d = Path(archive_dir or DEFAULT_ARCHIVE)
    d.mkdir(parents=True, exist_ok=True)
    digest = sha256_bytes(data)
    target = d / filename

    if target.exists() and sha256_bytes(target.read_bytes()) == digest:
        return {"path": str(target), "sha256": digest, "bytes": len(data), "existing": True}

    target.write_bytes(data)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}\t{filename}\t{digest}\t{len(data)}\t{method}\t{url}\n"
    with open(d / MANIFEST, "a") as fh:
        fh.write(line)
    return {"path": str(target), "sha256": digest, "bytes": len(data), "existing": False}
