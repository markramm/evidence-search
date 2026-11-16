"""Archive on retrieval — never as an afterthought (spec P5).

Workers currently hash and file by hand, and sometimes skip it: on 2026-08-19 a
worker read three court opinions via API/OCR and archived none of them, caught
only at QC. If the tool fetches it, the tool archives it.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import secrets
import time
from pathlib import Path

from .store import _resolve

try:
    import fcntl
except ImportError:          # not POSIX: no advisory lock, link() still refuses to replace
    fcntl = None

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


#: Serialises check-place-append across processes. Dot-prefixed, so no archived
#: document can take its name (leading dots are stripped from filenames).
LOCK = ".MANIFEST-evidence-search.lock"
_MAX_NAME = 180     # bytes; leaves room for ".<sha256>" inside a 255-byte component
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def safe_name(filename: str) -> str:
    """Reduce a filename to one plain path component.

    Filenames carry query-derived text, so `../`, an absolute path, a NUL or a
    leading dot must not choose where bytes land. Neutralised rather than
    rejected: a fetch that already succeeded should still be archived. Two
    names that collapse to the same component are kept apart by `archive`'s
    never-overwrite rule, not here.
    """
    s = "".join("_" if (c in "/\\" or ord(c) < 32 or ord(c) == 127) else c
                for c in str(filename))
    s = s.lstrip(". ").rstrip() or "unnamed"
    if s == MANIFEST:
        s = "_" + s
    if len(s.encode()) > _MAX_NAME:
        stem, ext = os.path.splitext(s)
        if len(ext.encode()) > 20:
            stem, ext = s, ""
        keep = _MAX_NAME - len(ext.encode())
        s = (stem.encode()[:keep].decode("utf-8", "ignore") or "unnamed") + ext
    return s


def _file_sha256(path: Path) -> str | None:
    """SHA-256 of a regular file, or None if it is missing, a symlink or not a file."""
    try:
        fd = os.open(path, os.O_RDONLY | _NOFOLLOW | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    with open(fd, "rb") as fh:
        if not os.path.isfile(fh.fileno()):
            return None
        h = hashlib.sha256()
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
        return h.hexdigest()


@contextlib.contextmanager
def _locked(root: Path):
    if fcntl is None:
        yield
        return
    fd = os.open(root / LOCK, os.O_RDWR | os.O_CREAT | _NOFOLLOW, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)        # closing releases the lock


def _place(root: Path, name: str, data: bytes, digest: str) -> tuple[Path, bool]:
    """Put `data` in the archive without replacing anything. -> (path, existing).

    The requested name is used if it is free or already holds these bytes;
    otherwise the bytes go under `<stem>.<sha12><ext>` (the full hash if even
    that is taken by something else), so a manifest line and its file can
    always be matched by hash. The file appears by link(), which fails rather
    than replaces, and a symlink sitting at a name counts as "taken": it is
    neither read through nor written through.
    """
    stem, ext = os.path.splitext(name)
    for candidate in (name, f"{stem}.{digest[:12]}{ext}", f"{stem}.{digest}{ext}"):
        target = root / candidate
        if not os.path.lexists(target):
            tmp = root / f".tmp-{os.getpid()}-{secrets.token_hex(8)}"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW, 0o644)
            try:
                with open(fd, "wb") as fh:
                    fh.write(data)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.link(tmp, target)
                return target, False
            except FileExistsError:
                pass            # lost a race for the name; judge what is there now
            finally:
                os.unlink(tmp)
        if _file_sha256(target) == digest:
            return target, True
    raise FileExistsError(
        f"cannot archive {name!r}: every candidate name holds different content")


def _field(value) -> str:
    """One manifest field: a tab or newline in it would forge or tear a line."""
    return str(value).replace("\t", "%09").replace("\n", "%0A").replace("\r", "%0D")


def archive(data: bytes, filename: str, url: str, method: str,
            archive_dir: Path | None = None) -> dict:
    """Store bytes under a SHA-256 and append a manifest line. Never overwrites.

    Returns {path, name, sha256, bytes, existing}. `existing` is True when
    these exact bytes were already archived under that name: nothing is written
    but the manifest still gains a line, because a second retrieval of the same
    bytes on a later day is itself evidence. Different bytes under a name in
    use are stored beside the earlier ones -- see `_place` -- so `path` is not
    always `<archive>/<filename>`; use the returned value.

    Manifest line (tab-separated, append-only):
        retrieved  stored-name  sha256  bytes  method  url  [requested-name]
    The seventh field appears only when the stored name differs from the name
    asked for. Lines are appended whole, under a lock shared by every process
    using this archive, so parallel workers neither lose nor interleave them.
    """
    d = Path(archive_dir or DEFAULT_ARCHIVE)
    d.mkdir(parents=True, exist_ok=True)
    root = Path(os.path.realpath(d))
    digest = sha256_bytes(data)
    name = safe_name(filename)

    with _locked(root):
        # Open the manifest first: if it cannot be written, archive nothing
        # rather than leave bytes on disk that no manifest line accounts for.
        mfd = os.open(root / MANIFEST, os.O_RDWR | os.O_APPEND | os.O_CREAT | _NOFOLLOW, 0o644)
        try:
            target, existing = _place(root, name, data, digest)
            if target.parent != root or target.name in ("", ".", ".."):
                raise ValueError(f"archive path escaped its root: {target}")
            fields = [time.strftime("%Y-%m-%d %H:%M:%S"), target.name, digest,
                      str(len(data)), _field(method), _field(url)]
            if target.name != str(filename):
                fields.append(_field(filename).replace("\x00", "%00"))
            line = "\t".join(fields) + "\n"
            size = os.fstat(mfd).st_size
            if size and os.pread(mfd, 1, size - 1) != b"\n":
                line = "\n" + line          # a crash left a torn last line
            os.write(mfd, line.encode("utf-8", "replace"))
            os.fsync(mfd)
        finally:
            os.close(mfd)
    return {"path": str(target), "name": target.name, "sha256": digest,
            "bytes": len(data), "existing": existing}


def verify(archive_dir: Path | None = None) -> dict:
    """Check every manifest line against the file it names. Read-only.

    Returns counts plus the failing entries: `missing` (no such file) and
    `mismatched` (the file's bytes no longer hash to the recorded digest --
    which is what the old overwrite-on-refetch left behind: a line for bytes
    that were since replaced). `unrecorded` lists files no line accounts for.
    Creates nothing, locks nothing, and reads both the current manifest and
    the six-field lines written before versioned names existed.
    """
    root = Path(os.path.realpath(Path(archive_dir or DEFAULT_ARCHIVE)))
    rep = {"archive": str(root), "entries": 0, "ok": 0, "malformed": 0,
           "missing": [], "mismatched": [], "unrecorded": []}
    try:
        text = (root / MANIFEST).read_text(errors="replace")
    except OSError:
        text = ""
    seen: dict[str, str | None] = {}
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        f = raw.split("\t")
        if len(f) < 6 or len(f[2]) != 64 or f[1] in ("", ".", "..") or "/" in f[1]:
            rep["malformed"] += 1
            continue
        stamp, name, digest = f[0], f[1], f[2]
        rep["entries"] += 1
        if name not in seen:
            seen[name] = (_file_sha256(root / name)
                          if os.path.lexists(root / name) else "absent")
        entry = {"retrieved": stamp, "name": name, "sha256": digest, "url": f[5]}
        if seen[name] == "absent":
            rep["missing"].append(entry)
        elif seen[name] != digest:
            rep["mismatched"].append({**entry, "actual": seen[name]})
        else:
            rep["ok"] += 1
    if root.is_dir():
        rep["unrecorded"] = sorted(
            n for n in os.listdir(root)
            if n not in seen and n != MANIFEST and not n.startswith("."))
    return rep


if __name__ == "__main__":      # python -m evidence_search.core.archive verify [DIR] [--json]
    import json
    import sys

    args = [a for a in sys.argv[1:] if a != "--json"]
    if not args or args[0] != "verify" or len(args) > 2:
        sys.exit("usage: python -m evidence_search.core.archive verify [DIR] [--json]")
    report = verify(args[1] if len(args) == 2 else None)
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2))
    else:
        print(f"{report['archive']}: {report['entries']} entries, {report['ok']} ok, "
              f"{len(report['missing'])} missing, {len(report['mismatched'])} hash-mismatched, "
              f"{report['malformed']} malformed lines, {len(report['unrecorded'])} unrecorded files")
        for kind in ("missing", "mismatched"):
            for e in report[kind]:
                print(f"  {kind}\t{e['retrieved']}\t{e['name']}\t{e['sha256'][:12]}")
    sys.exit(1 if report["missing"] or report["mismatched"] else 0)
