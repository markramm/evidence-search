"""The humanomation worklist.

`gate resume` archived whatever it was handed, with a SHA-256, as the record of
that search. For a tool whose product is provenance, that is the one place a
human slip becomes a permanent false record.
"""
import pathlib
import tempfile

from cascade_search.core.gate_page import render
from cascade_search.core.gateui import collect, validate, _describe
from cascade_search.core.store import Store


def _store_with(*payloads):
    s = Store(pathlib.Path(tempfile.mkdtemp()) / "t.db")
    for p in payloads:
        s.create_job("oscn", "awaiting_human", p)
    return s


OSCN = {"url": "https://www.oscn.net/dockets/Results.aspx?db=caddo&lname=Frazier"
                "&FiledDateL=1/1/2013&FiledDateH=12/31/2013",
        "query": "Frazier caddo 2013", "gate_type": "captcha",
        "capture": ["page HTML after passing the challenge"]}


def test_gate_reads_as_english_not_url_fragments():
    g = _describe({"token": "t", "source": "oscn", "payload": OSCN, "created_at": 0})
    assert g["seeking"] == ("Every Caddo County case filed against a party named "
                            "Frazier in 2013.")


def test_null_payload_fields_never_reach_the_operator():
    """open_gate() and browser.fetch() write different shapes; one leaves nulls."""
    g = _describe({"token": "t", "source": "oscn", "created_at": 0,
                   "payload": {"url": OSCN["url"], "query": "q",
                               "gate_type": None, "capture": None}})
    assert g["gate"] and g["gate"] != "None"
    assert g["capture"] and "None" not in str(g["capture"])
    assert "None" not in render([{**g, "also": []}])


def test_duplicate_targets_collapse_to_one_card():
    """The live queue held the same OSCN search parked twice.

    Creation now dedupes on URL, so new duplicates cannot arise -- but the UI
    must still collapse rows that predate that fix, and rows whose payloads
    differ while pointing at the same target. Built by writing jobs directly,
    since create_job would now refuse to make a second one.
    """
    import json as _json
    import time as _time
    s = _store_with(OSCN)
    # a legacy duplicate, as it would sit in an existing store
    s.conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?)",
                   ("legacy0000dup", "oscn", "awaiting_human",
                    _json.dumps(OSCN), _time.time(), _time.time(), None))
    cards = collect(s)
    assert len(cards) == 1
    assert len(cards[0]["also"]) == 1
    assert "clears 2" in render(cards)


def test_rejects_the_challenge_page_itself():
    g = _describe({"token": "t", "source": "oscn", "payload": OSCN, "created_at": 0})
    ok, msg = validate(g, "<html>Verify you are human</html>" + "x" * 400)
    assert not ok and "challenge" in msg.lower()


def test_rejects_a_page_about_something_else():
    g = _describe({"token": "t", "source": "oscn", "payload": OSCN, "created_at": 0})
    ok, msg = validate(g, "<html>" + "unrelated tariff coverage " * 40 + "</html>")
    assert not ok and "Frazier" in msg


def test_accepts_a_no_records_page_as_a_finding():
    """An empty docket is a publishable negative, not a failed capture."""
    g = _describe({"token": "t", "source": "oscn", "payload": OSCN, "created_at": 0})
    ok, msg = validate(g, "<html>" + "OSCN caddo docket. Found No Records. " * 10 + "</html>")
    assert ok and "finding" in msg


def test_accepts_the_real_thing():
    g = _describe({"token": "t", "source": "oscn", "payload": OSCN, "created_at": 0})
    ok, _ = validate(g, "<html>" + "OSCN caddo FRAZIER, ANDREA CF-2013-00038 " * 12 + "</html>")
    assert ok


def test_empty_queue_says_so_without_scolding():
    page = render([])
    assert "Nothing is waiting on you" in page
    assert "<article" not in page
