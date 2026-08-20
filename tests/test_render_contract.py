"""What the human-readable output must never let a worker conclude.

Every case here is a mistake a real worker made, or nearly made, in the field
log. The mechanism was right in each one; the RENDERING was what misled. These
are rendering tests because that is where the errors actually lived.
"""
from evidence_search.cli import _emit, main, EXIT_ACCESS_BLOCKER
from evidence_search.core.results import (AccessBlocker, Blocker, Coverage, Hit,
                                         Result)


def _hit(n, total=None, **meta):
    m = dict(meta)
    if total is not None:
        m["total_matches"] = total
    return Hit(query="q", coverage=Coverage(queried=["s"], responsive=["s"]),
               results=[Result(url=f"https://e/{i}", title=f"r{i}", source="s",
                               engines=["s"], meta=m if i == 0 else {})
                        for i in range(n)])


# ---- total_matches: "at least 20" is almost never the honest answer ----------

def test_page_count_never_stands_alone_when_a_total_is_known(capsys):
    """20 rows standing for 293 matches was reachable only by --json.

    A worker following the skill doc's most-repeated caution had to write a
    recursive JSON walker to find total_matches nested inside results[0]. On a
    beat where scale IS the claim, an unqualified `results: 20` invites citing
    the page size as the finding.
    """
    _emit(_hit(20, total=293), as_json=False)
    out = capsys.readouterr().out
    assert "293" in out
    assert "cite 293, not 20" in out


def test_no_total_line_invented_when_the_page_is_the_whole_corpus(capsys):
    """Sources that return everything must not imply a hidden remainder."""
    _emit(_hit(3, total=3), as_json=False)
    out = capsys.readouterr().out
    assert "total_matches" not in out
    assert "results:  3" in out


def test_no_total_line_when_the_source_reports_none(capsys):
    _emit(_hit(3), as_json=False)
    assert "total_matches" not in capsys.readouterr().out


# ---- --limit trims the view, never the denominator --------------------------

def test_limit_trims_display_but_preserves_the_true_total(capsys):
    """A shortened list must never read as a smaller corpus.

    This is the invariant that makes display-side --limit safe: trimming rows
    is honest only while the header still reports what was actually there.
    """
    _emit(_hit(40, total=1200), as_json=False, limit=5)
    out = capsys.readouterr().out
    assert "1200" in out
    assert "r5" not in out


def test_limit_synthesises_the_denominator_when_the_source_gave_none(capsys):
    """Trimming 40 rows to 5 must not silently turn 40 into 5."""
    _emit(_hit(40), as_json=False, limit=5)
    out = capsys.readouterr().out
    assert "of 40 total_matches" in out


def test_limit_above_result_count_changes_nothing(capsys):
    _emit(_hit(3), as_json=False, limit=25)
    assert "total_matches" not in capsys.readouterr().out


def test_limit_is_spelled_the_same_on_every_listing_source():
    """Learning the flag six times cost a round-trip per source."""
    import argparse
    import contextlib
    import io
    for cmd in ("news", "courtlistener", "propublica", "web", "usaspending",
                "crossref", "fedreg", "docs"):
        buf = io.StringIO()
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
            main([cmd, "--help"])
        assert "--limit" in buf.getvalue(), f"{cmd} does not accept --limit"


def test_upstream_flag_names_still_work():
    """--rows and --per-page are in field commands already; keep them working."""
    import argparse
    import contextlib
    import io
    for cmd, alias in (("crossref", "--rows"), ("fedreg", "--per-page")):
        buf = io.StringIO()
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
            main([cmd, "--help"])
        assert alias in buf.getvalue(), f"{cmd} dropped {alias}"


# ---- 404 is a wrong path, not a wall ----------------------------------------

def _blocked(mech):
    return AccessBlocker(query="q", coverage=Coverage(), mechanism=mech,
                         url="https://example.gov/x", detail=str(mech.value))


def test_404_is_not_reported_as_a_block(capsys):
    """`AccessBlocker: http-404` on /about read as suppression.

    A worker nearly wrote that a trade publication was blocking its own
    about-page; the path was simply /about-us. On this beat that is a spicy and
    completely false claim -- the overclaim the tool exists to prevent, arriving
    from inside the tool.
    """
    _emit(_blocked(Blocker.NOT_FOUND), as_json=False)
    out = capsys.readouterr().out
    assert "Access was blocked" not in out
    assert "does not exist" in out
    assert "ESCALATABLE" not in out


def test_404_names_the_enumeration_case(capsys):
    """~1 in 7 ids in a sequential sweep 404s. That is signal, not a wall."""
    _emit(_blocked(Blocker.NOT_FOUND), as_json=False)
    assert "never issued" in capsys.readouterr().out


def test_real_walls_still_read_as_walls(capsys):
    """The split must not soften an actual block."""
    for mech in (Blocker.CLOUDFLARE, Blocker.TURNSTILE, Blocker.FORBIDDEN,
                 Blocker.DATADOME, Blocker.AUTH_WALL):
        _emit(_blocked(mech), as_json=False)
        out = capsys.readouterr().out
        assert "NOT a negative finding. Access was blocked." in out, mech
        assert "does not exist" not in out, mech


def test_escalation_hint_survives_for_browser_passable_gates(capsys):
    _emit(_blocked(Blocker.TURNSTILE), as_json=False)
    assert "ESCALATABLE" in capsys.readouterr().out


def test_404_still_exits_blocked_not_absent():
    """Register changed; the contract did not. A 404 is never exit 1."""
    assert _emit(_blocked(Blocker.NOT_FOUND), as_json=True) == EXIT_ACCESS_BLOCKER


def test_is_wall_separates_the_two_classes():
    assert _blocked(Blocker.NOT_FOUND).is_wall is False
    assert _blocked(Blocker.TURNSTILE).is_wall is True
