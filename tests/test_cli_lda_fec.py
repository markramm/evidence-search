"""CLI wiring for `lda` and `fec`: argparse plumbing end-to-end, usage errors,
and that a typo in a `dest=`/kwarg name would actually be caught here rather
than only inside each source's own unit tests (which call the Python API
directly and would never notice a CLI-layer mismatch).
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from evidence_search import cli
from evidence_search.core.results import Coverage, Hit, Result, VerifiedAbsence


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("evidence_search.core.store.DEFAULT_DB", tmp_path / "store.db")
    monkeypatch.setattr("evidence_search.core.archive.DEFAULT_ARCHIVE", tmp_path / "archive")
    return tmp_path


# --- lda -------------------------------------------------------------------

def test_lda_requires_client_or_registrant(tmp_cache, capsys):
    rc = cli.main(["lda"])
    assert rc == cli.EXIT_USAGE
    assert "client" in capsys.readouterr().err


def test_lda_dispatches_with_parsed_args(tmp_cache, monkeypatch, capsys):
    captured = {}

    def _fake_search(**kw):
        captured.update(kw)
        return Hit(query="x", coverage=Coverage(queried=["lda"], responsive=["lda"]),
                  results=[Result(url="u", title="t", source="lda")])

    monkeypatch.setattr("evidence_search.sources.lda.search", _fake_search)
    rc = cli.main(["lda", "--client", "Purdue Pharma", "--year", "2008",
                   "--year", "2009", "--bill", "H.R. 2994"])
    assert rc == cli.EXIT_HIT
    assert captured["client_name"] == "Purdue Pharma"
    assert captured["years"] == [2008, 2009]
    assert captured["bill"] == "H.R. 2994"


def test_lda_registrant_only_is_accepted(tmp_cache, monkeypatch):
    """--registrant alone (no --client) must reach the dispatcher, not EXIT_USAGE."""
    captured = {}

    def _fake_search(**kw):
        captured.update(kw)
        return Hit(query="x", coverage=Coverage(queried=["lda"], responsive=["lda"]), results=[])

    monkeypatch.setattr("evidence_search.sources.lda.search", _fake_search)
    rc = cli.main(["lda", "--registrant", "Some Firm"])
    assert rc == cli.EXIT_HIT
    assert captured["registrant_name"] == "Some Firm"
    assert captured["client_name"] is None


# --- fec ---------------------------------------------------------------------

def test_fec_requires_cycles(tmp_cache, capsys):
    rc = cli.main(["fec", "--from-committee", "C00370643", "--to-committee", "C00343863"])
    assert rc == cli.EXIT_USAGE
    assert "cycles" in capsys.readouterr().err


def test_fec_rejects_bad_cycle_format(tmp_cache, capsys):
    rc = cli.main(["fec", "--from-committee", "C00370643", "--to-committee", "C00343863",
                   "--cycles", "not-a-cycle"])
    assert rc == cli.EXIT_USAGE
    assert "fec:" in capsys.readouterr().err


def test_fec_requires_both_committees(tmp_cache, capsys):
    rc = cli.main(["fec", "--cycles", "2008", "--from-committee", "C00370643"])
    assert rc == cli.EXIT_USAGE
    assert "from-committee" in capsys.readouterr().err or "to-committee" in capsys.readouterr().err


def test_fec_between_dispatches_with_parsed_cycles_and_cache_dir(tmp_cache, monkeypatch):
    captured = {}

    def _fake_between(from_committee, to_committee, cycle_from, cycle_to, **kw):
        captured.update(from_committee=from_committee, to_committee=to_committee,
                        cycle_from=cycle_from, cycle_to=cycle_to, **kw)
        return Hit(query="x", coverage=Coverage(queried=["fec"], responsive=["fec"]), results=[])

    monkeypatch.setattr("evidence_search.sources.fec.between", _fake_between)
    cache = pathlib.Path(tempfile.mkdtemp()) / "cache"
    rc = cli.main(["fec", "--from-committee", "C00370643", "--to-committee", "C00343863",
                   "--cycles", "2006-2014", "--cache-dir", str(cache)])
    assert rc == cli.EXIT_HIT
    assert captured["from_committee"] == "C00370643"
    assert captured["to_committee"] == "C00343863"
    assert captured["cycle_from"] == 2006 and captured["cycle_to"] == 2014
    assert captured["cache_dir"] == cache


def test_fec_committee_lookup_dispatches(tmp_cache, monkeypatch):
    captured = {}

    def _fake_lookup(name, cycle_from, cycle_to, **kw):
        captured.update(name=name, cycle_from=cycle_from, cycle_to=cycle_to, **kw)
        return VerifiedAbsence(query="x", coverage=Coverage(queried=["fec"], responsive=["fec"]),
                               searched="no match")

    monkeypatch.setattr("evidence_search.sources.fec.lookup_committee", _fake_lookup)
    rc = cli.main(["fec", "--committee", "Rogers", "--cycles", "2008"])
    assert rc == cli.EXIT_VERIFIED_ABSENCE
    assert captured["name"] == "Rogers"
    assert captured["cycle_from"] == 2008 and captured["cycle_to"] == 2008
