"""`extract` may say "the terms are not in it" only over a document it read.

It printed that sentence over Incapsula and Akamai stub pages served with a
200, over a remote scanned PDF read with --no-ocr, and after a pdftotext
timeout had silently sent a text-layer PDF to a capped OCR. The transport is
stubbed at httpx; nothing here touches the network.
"""
from __future__ import annotations

import httpx
import pytest


class _Resp:
    def __init__(self, code, text, headers=None):
        self.status_code = code
        self.text = text
        self.headers = headers or {}
        self.content = text.encode()


@pytest.fixture
def serve(monkeypatch):
    def _serve(code, text, headers=None):
        monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp(code, text, headers))
    return _serve



INCAPSULA_STUB = (
    '<html><head><META NAME="robots" CONTENT="noindex,nofollow">'
    '<script src="/_Incapsula_Resource?SWJIYLWA=5074a744e2e3d891814e9a2dace20bd4,'
    '719d34d31c8e3a6e6fffd425f7e032f3"></script></head><body></body></html>')
AKAMAI_DENIED = (
    "<html><head><title>Access Denied</title></head><body><h1>Access Denied</h1>"
    "You don't have permission to access \"http://www.example.gov/x\" on this server."
    "<p>Reference #18.abc123.1699999999.1a2b3c</p></body></html>")


@pytest.mark.parametrize("stub", [INCAPSULA_STUB, AKAMAI_DENIED])
def test_extract_refuses_a_negative_over_a_wall_stub(serve, capsys, stub):
    from evidence_search.cli import EXIT_ACCESS_BLOCKER, main
    serve(200, stub)
    code = main(["extract", "https://www.example.gov/x", "--grep", "Jane Doe"])
    out = capsys.readouterr().out
    assert code == EXIT_ACCESS_BLOCKER
    assert "the document was read" not in out


def test_extract_refuses_a_negative_over_an_unrecognised_stub(serve, capsys):
    """No known signature, almost no visible text: still not a document."""
    from evidence_search.cli import EXIT_ACCESS_BLOCKER, main
    serve(200, "<html><head><script>" + "var a=1;" * 3000 + "</script></head>"
               "<body><div>Loading</div></body></html>")
    code = main(["extract", "https://www.example.gov/x", "--grep", "Jane Doe"])
    out = capsys.readouterr().out
    assert code == EXIT_ACCESS_BLOCKER
    assert "NOT an absence" in out and "the document was read" not in out


def test_extract_still_reports_a_bounded_negative_over_a_real_page(serve, capsys):
    from evidence_search.cli import main
    serve(200, "<html><body>" + "<p>The board approved the contract.</p>" * 40
               + "</body></html>")
    assert main(["extract", "https://www.example.gov/x", "--grep", "Jane Doe"]) == 0
    assert "IN THIS DOCUMENT ONLY" in capsys.readouterr().out


def test_grep_reads_the_headline_and_byline_in_an_article_header():
    from evidence_search.core import extract as X
    html = ("<body><header><nav>Home</nav><span>The Daily Site</span></header>"
            "<article><header><h1>Smith indicted</h1><p>By Jane Doe</p></header><p>"
            + "body " * 200 + "</p></article></body>")
    hits = {m["pattern"] for m in X.grep(html, ["Jane Doe", "Smith indicted"])}
    assert hits == {"Jane Doe", "Smith indicted"}
    assert "The Daily Site" not in X.page_text(html), "site chrome is still stripped"


def test_pdftotext_timeout_is_a_failure_not_an_empty_text_layer(monkeypatch, tmp_path):
    """Returning "" on timeout sent a large text-layer PDF to a 50-page OCR."""
    import subprocess
    from evidence_search.core import pdf

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pdftotext", timeout=1)

    monkeypatch.setattr(pdf, "have", lambda tool: True)
    monkeypatch.setattr(pdf.subprocess, "run", _timeout)
    monkeypatch.setattr(pdf, "ocr", lambda *a, **k: pytest.fail("OCR ran after a timeout"))
    with pytest.raises(pdf.PdfReadFailed):
        pdf.read(tmp_path / "big.pdf")


def _remote_pdf(monkeypatch, text_layer):
    import evidence_search.core.http as http
    from evidence_search.core import pdf as pdfmod
    pdf_bytes = b"%PDF-1.7\n" + b"binary" * 200
    monkeypatch.setattr(http, "fetch", lambda url, **kw: (
        (pdf_bytes if kw.get("binary") else "%PDF-1.7\nmangled"), None))
    monkeypatch.setattr(pdfmod, "text_layer", text_layer)


def test_remote_scanned_pdf_with_no_ocr_is_refused_like_a_local_one(monkeypatch, capsys):
    from evidence_search.cli import EXIT_ACCESS_BLOCKER, main
    _remote_pdf(monkeypatch, lambda p, timeout=120: "  stamp ")
    code = main(["extract", "https://example.gov/x.pdf", "--grep", "11101", "--no-ocr"])
    out = capsys.readouterr().out
    assert code == EXIT_ACCESS_BLOCKER
    assert "Scanned PDF" in out and "the document was read" not in out


def test_remote_pdf_read_failure_is_a_blocker_not_a_crash(monkeypatch, capsys):
    from evidence_search.cli import EXIT_ACCESS_BLOCKER, main
    from evidence_search.core import pdf as pdfmod

    def _fail(p, timeout=120):
        raise pdfmod.PdfReadFailed("pdftotext timed out")

    _remote_pdf(monkeypatch, _fail)
    code = main(["extract", "https://example.gov/x.pdf", "--grep", "11101"])
    assert code == EXIT_ACCESS_BLOCKER
    assert "timed out" in capsys.readouterr().out
