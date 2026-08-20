"""The humanomation worklist.

`gate resume` archived whatever it was handed, with a SHA-256, as the record of
that search. For a tool whose product is provenance, that is the one place a
human slip becomes a permanent false record.
"""
import pathlib
import tempfile

from evidence_search.core.gate_page import render
from evidence_search.core.gateui import collect, validate, _describe
from evidence_search.core.store import Store


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


# --- origin discipline on /resume ---------------------------------------------
# The endpoint archives bytes under a SHA-256 and closes a gate against them. A
# page the operator happens to have open must not be able to write that record.

import json as _json
import socket
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

from evidence_search.core.gateui import _handler


class _Server:
    def __init__(self, store):
        self.httpd = HTTPServer(("127.0.0.1", 0), _handler(store.path))
        self.port = self.httpd.server_address[1]
        self.t = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.t.start()

    def post(self, body=b"{}", ctype="application/json", origin=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/resume", data=body, method="POST")
        req.add_header("Content-Type", ctype)
        if origin:
            req.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def raw_post(self, host, body=b'{"token":"t"}'):
        """Send a literal Host header. urllib resolves the name instead of
        sending it verbatim, but a rebinding attack puts it on the wire."""
        req = (f"POST /resume HTTP/1.1\r\nHost: {host}\r\n"
               f"Content-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
               ).encode() + body
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(req)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
        return int(buf.split(b" ")[1])

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _serve():
    return _Server(_store_with(OSCN))


def test_resume_refuses_a_foreign_host_header():
    """DNS rebinding: an attacker name resolving to 127.0.0.1 is same-origin
    to the browser, so the Host header is the only thing that catches it."""
    srv = _serve()
    try:
        assert srv.raw_post("evil.example.com") == 403
        assert srv.raw_post(f"127.0.0.1:{srv.port}") != 403   # our own page is fine
    finally:
        srv.close()


def test_resume_refuses_a_cross_site_form_post():
    """enctype=text/plain is a CORS simple request -- no preflight. Requiring
    JSON is what puts the preflight back."""
    srv = _serve()
    try:
        code, _ = srv.post(body=b'{"token":"t","body":"x"}', ctype="text/plain")
        assert code == 403
    finally:
        srv.close()


def test_resume_refuses_a_cross_site_origin():
    srv = _serve()
    try:
        code, _ = srv.post(origin="https://evil.example.com")
        assert code == 403
    finally:
        srv.close()


def test_resume_still_accepts_the_local_page():
    """The guard must not break the operator's own click."""
    srv = _serve()
    try:
        code, raw = srv.post(body=_json.dumps({"token": "nope", "body": "x"}).encode(),
                             origin=f"http://127.0.0.1:{srv.port}")
        # Reaches the handler proper: unknown token, not a refused origin.
        assert code == 404
        assert "no longer open" in _json.loads(raw)["message"]
    finally:
        srv.close()


def test_host_check_accepts_loopback_spellings():
    """Host is case-insensitive (RFC 7230) and a portless IPv6 literal is
    bracketed. rsplit-then-strip turned "[::1]" into ":" and refused it."""
    srv = _serve()
    try:
        for host in (f"127.0.0.1:{srv.port}", "localhost", "LOCALHOST",
                     f"LocalHost:{srv.port}", "[::1]", f"[::1]:{srv.port}"):
            assert srv.raw_post(host) != 403, f"refused a local request from {host!r}"
    finally:
        srv.close()


def test_host_check_still_refuses_foreign_names():
    srv = _serve()
    try:
        for host in ("evil.example.com", "attacker.test:8787",
                     "127.0.0.1.evil.com", "notlocalhost"):
            assert srv.raw_post(host) == 403, f"accepted a foreign host {host!r}"
    finally:
        srv.close()
