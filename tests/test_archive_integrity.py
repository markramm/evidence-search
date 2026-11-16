"""The archive is evidence: bytes are never replaced, and never leave the root.

Two defects made the archive unsafe to cite. Archiving different bytes under a
name already in use REPLACED the file while the manifest kept the hash of the
earlier bytes -- the earlier evidence was destroyed and the manifest described
bytes that no longer existed. And a filename carrying `../` (filenames can hold
query-derived text) wrote outside the archive directory.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from evidence_search.core import archive as arch
from evidence_search.core.archive import archive, sha256_bytes, verify

ROOT = Path(__file__).resolve().parents[1]


def _lines(d: Path) -> list[list[str]]:
    text = (d / arch.MANIFEST).read_text()
    assert text.endswith("\n")
    return [ln.split("\t") for ln in text.splitlines()]


# --- never overwrite ---------------------------------------------------------

def test_different_bytes_under_one_name_keeps_both(tmp_path):
    a = archive(b"first", "page.html", "http://x/1", "m", tmp_path)
    b = archive(b"second", "page.html", "http://x/2", "m", tmp_path)

    assert Path(a["path"]).read_bytes() == b"first"       # earlier evidence intact
    assert Path(b["path"]).read_bytes() == b"second"
    assert a["path"] != b["path"]
    assert Path(b["path"]).name == f"page.{sha256_bytes(b'second')[:12]}.html"
    assert not a["existing"] and not b["existing"]

    # each manifest line names a file whose bytes hash to that line's digest
    rows = _lines(tmp_path)
    assert len(rows) == 2
    for _stamp, name, digest, size, _method, _url in (r[:6] for r in rows):
        data = (tmp_path / name).read_bytes()
        assert sha256_bytes(data) == digest and len(data) == int(size)
    assert len(rows[0]) == 6 and rows[1][6] == "page.html"   # version traces to its name
    assert [r[5] for r in rows] == ["http://x/1", "http://x/2"]
    rep = verify(tmp_path)
    assert rep["ok"] == 2 and not rep["missing"] and not rep["mismatched"]


def test_same_bytes_is_a_noop_that_records_the_retrieval(tmp_path):
    a = archive(b"same", "page.html", "http://x", "m", tmp_path)
    before = os.stat(a["path"])
    b = archive(b"same", "page.html", "http://x", "m", tmp_path)
    after = os.stat(b["path"])

    assert b["existing"] is True and b["path"] == a["path"]
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.startswith(".")) == [
        arch.MANIFEST, "page.html"]
    rows = _lines(tmp_path)
    assert len(rows) == 2 and rows[0][1:4] == rows[1][1:4]   # second retrieval recorded


def test_refetching_a_second_version_is_also_idempotent(tmp_path):
    archive(b"v1", "p.html", "u", "m", tmp_path)
    b = archive(b"v2", "p.html", "u", "m", tmp_path)
    c = archive(b"v2", "p.html", "u", "m", tmp_path)
    assert c["existing"] is True and c["path"] == b["path"]
    assert len([p for p in tmp_path.iterdir() if p.name.startswith("p.")]) == 2


def test_occupied_versioned_name_is_not_overwritten_either(tmp_path):
    archive(b"v1", "p.html", "u", "m", tmp_path)
    squatter = tmp_path / f"p.{sha256_bytes(b'v2')[:12]}.html"
    squatter.write_bytes(b"something else")
    b = archive(b"v2", "p.html", "u", "m", tmp_path)
    assert squatter.read_bytes() == b"something else"
    assert Path(b["path"]).read_bytes() == b"v2"
    assert Path(b["path"]).name == f"p.{sha256_bytes(b'v2')}.html"


# --- one path component, inside the root -------------------------------------

@pytest.mark.parametrize("name", [
    "../escape.html", "../../etc/passwd", "/abs/olute.html", "a/b/c.html",
    "..", ".", "", ".hidden", "..\\..\\win.html", "nul\x00byte.html",
    "tab\tand\nnewline.html", "x" * 600 + ".html", "é" * 300 + ".pdf",
])
def test_hostile_filenames_stay_inside_the_archive(tmp_path, name):
    root = tmp_path / "archive"
    rec = archive(b"data", name, "http://x", "m", root)
    p = Path(rec["path"])

    assert p.parent == root.resolve()
    assert p.read_bytes() == b"data"
    assert not p.name.startswith(".") and "\x00" not in p.name
    assert len(p.name.encode()) <= 255
    # nothing landed outside the root
    assert sorted(x.name for x in tmp_path.iterdir()) == ["archive"]
    rows = _lines(root)
    assert len(rows) == 1 and len(rows[0]) >= 6 and rows[0][1] == p.name
    assert verify(root)["ok"] == 1


def test_long_name_keeps_its_extension(tmp_path):
    rec = archive(b"d", "y" * 600 + ".pdf", "u", "m", tmp_path)
    assert rec["path"].endswith(".pdf")


def test_names_that_collide_after_sanitising_do_not_overwrite(tmp_path):
    a = archive(b"one", "a/b.html", "u", "m", tmp_path)
    b = archive(b"two", "a_b.html", "u", "m", tmp_path)
    assert a["path"] != b["path"]
    assert Path(a["path"]).read_bytes() == b"one"
    assert Path(b["path"]).read_bytes() == b"two"


def test_the_manifest_cannot_be_archived_over(tmp_path):
    rec = archive(b"not a manifest", arch.MANIFEST, "u", "m", tmp_path)
    assert Path(rec["path"]).name != arch.MANIFEST
    assert len(_lines(tmp_path)) == 1 and verify(tmp_path)["ok"] == 1


def test_a_symlink_in_the_archive_is_never_followed(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"precious")
    (root / "page.html").symlink_to(outside)

    rec = archive(b"fetched", "page.html", "u", "m", root)

    assert outside.read_bytes() == b"precious"
    assert (root / "page.html").is_symlink()
    p = Path(rec["path"])
    assert p.parent == root.resolve() and not p.is_symlink()
    assert p.read_bytes() == b"fetched"

    # same bytes as the link's target must not count as "already archived"
    rec2 = archive(b"precious", "page.html", "u", "m", root)
    assert not rec2["existing"] and not Path(rec2["path"]).is_symlink()


def test_a_symlinked_manifest_is_refused(tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep\n")
    (root / arch.MANIFEST).symlink_to(outside)
    with pytest.raises(OSError):
        archive(b"d", "p.html", "u", "m", root)
    assert outside.read_text() == "keep\n"


# --- verification ------------------------------------------------------------

def test_verify_reports_missing_and_mismatched(tmp_path):
    archive(b"good", "good.html", "u", "m", tmp_path)
    archive(b"gone", "gone.html", "u", "m", tmp_path)
    archive(b"orig", "changed.html", "u", "m", tmp_path)
    (tmp_path / "gone.html").unlink()
    (tmp_path / "changed.html").write_bytes(b"tampered")
    (tmp_path / "stray.html").write_bytes(b"no manifest line")

    rep = verify(tmp_path)
    assert rep["entries"] == 3 and rep["ok"] == 1
    assert [e["name"] for e in rep["missing"]] == ["gone.html"]
    assert [e["name"] for e in rep["mismatched"]] == ["changed.html"]
    assert rep["mismatched"][0]["actual"] == sha256_bytes(b"tampered")
    assert rep["unrecorded"] == ["stray.html"]


def test_verify_reads_a_legacy_manifest_and_changes_nothing(tmp_path):
    """The old code's damage: one name, two hashes, only the later bytes left."""
    old, new = b"earlier bytes", b"later bytes"
    (tmp_path / "doc.html").write_bytes(new)
    manifest = tmp_path / arch.MANIFEST
    manifest.write_text(
        f"2026-08-28 02:09:48\tdoc.html\t{sha256_bytes(old)}\t{len(old)}\tevidence-search:browser\thttp://x\n"
        f"2026-08-29 02:09:48\tdoc.html\t{sha256_bytes(new)}\t{len(new)}\tevidence-search:browser\thttp://x\n")
    snapshot = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}

    rep = verify(tmp_path)

    assert rep["entries"] == 2 and rep["ok"] == 1
    assert [e["sha256"] for e in rep["mismatched"]] == [sha256_bytes(old)]
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in tmp_path.iterdir()} == snapshot          # read-only, no lock file

    # and the new code appends to that manifest without rewriting what is there
    head = manifest.read_bytes()
    archive(b"third", "doc.html", "http://y", "m", tmp_path)
    assert manifest.read_bytes().startswith(head)
    assert (tmp_path / "doc.html").read_bytes() == new


def test_verify_on_an_absent_archive_creates_nothing(tmp_path):
    rep = verify(tmp_path / "nope")
    assert rep["entries"] == 0 and not (tmp_path / "nope").exists()


def test_append_repairs_a_torn_last_line(tmp_path):
    archive(b"a", "a.html", "u", "m", tmp_path)
    with open(tmp_path / arch.MANIFEST, "a") as fh:
        fh.write("2026-01-01 00:00:00\ttorn.html\tdead")          # crash mid-line
    archive(b"b", "b.html", "u", "m", tmp_path)
    rep = verify(tmp_path)
    assert rep["ok"] == 2 and rep["malformed"] == 1


# --- many processes at once --------------------------------------------------

WORKER = textwrap.dedent("""
    import sys
    from pathlib import Path
    from evidence_search.core.archive import archive
    d, w, n = Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    for i in range(n):
        # a name every worker fights over, with per-worker bytes ...
        archive(f"worker {w} round {i}".encode() * 50, "contested.html", f"http://x/{w}/{i}", "t", d)
        # ... the same bytes from everyone ...
        archive(b"identical" * 100, "shared.html", "http://x/shared", "t", d)
        # ... and a name of its own
        archive(f"own {w} {i}".encode(), f"own-{w}-{i}.html", "http://x/own", "t", d)
""")


def test_many_processes_lose_no_entries_and_tear_no_lines(tmp_path):
    d, workers, rounds = tmp_path / "archive", 12, 15
    env = {**os.environ, "PYTHONPATH": str(ROOT), "EVIDENCE_ARCHIVE": str(tmp_path / "unused")}
    procs = [subprocess.Popen([sys.executable, "-c", WORKER, str(d), str(w), str(rounds)],
                              env=env, stderr=subprocess.PIPE) for w in range(workers)]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()

    rows = _lines(d)
    assert len(rows) == workers * rounds * 3                    # nothing lost
    assert all(len(r) in (6, 7) and len(r[2]) == 64 for r in rows)   # nothing torn

    rep = verify(d)
    assert rep["entries"] == len(rows) and rep["ok"] == len(rows)
    assert not rep["missing"] and not rep["mismatched"] and not rep["malformed"]
    assert not rep["unrecorded"]

    # every contested version survived: one file per distinct byte string
    contested = {r[1] for r in rows if r[1].startswith("contested.")}
    assert len(contested) == workers * rounds
    assert len({r[1] for r in rows if r[1].startswith("shared.")}) == 1
    assert not [p for p in d.iterdir() if p.name.startswith(".tmp-")]
