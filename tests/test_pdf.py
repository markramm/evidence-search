"""PDF reading: exact by default, OCR only when there is no text to lose."""
import pathlib
import tempfile

import pytest

from cascade_search.core import pdf


def test_text_layer_threshold_is_about_scanned_vs_typed():
    """A scanned page yields a handful of stray glyphs, not zero.

    Thresholding at >0 would treat a cover-page stamp as a readable document.
    """
    assert pdf.TEXT_LAYER_MIN_CHARS > 0
    assert pdf.TEXT_LAYER_MIN_CHARS < 1000


def test_ocr_provenance_marks_text_unverified():
    """OCR text must never be mistaken for verbatim text.

    This beat runs on exact strings -- docket numbers, award IDs, dollar
    figures -- and OCR confuses 0/O, 1/l, 5/S. A wrong identifier archived with
    a SHA-256 looks authoritative, which is the confident-wrong-answer class
    this package exists to prevent.
    """
    import inspect
    src = inspect.getsource(pdf.ocr)
    assert '"ocr": True' in src
    assert '"human_verified": False' in src
    assert "caution" in src


def test_read_prefers_the_text_layer(monkeypatch, tmp_path):
    """OCR is slower and lossy; never use it when exact text is available."""
    monkeypatch.setattr(pdf, "text_layer", lambda p, timeout=120: "x" * 5000)
    called = []
    monkeypatch.setattr(pdf, "ocr", lambda *a, **k: called.append(1) or ("", {}))
    txt, prov = pdf.read(tmp_path / "f.pdf")
    assert prov["source"] == "text-layer"
    assert prov["ocr"] is False
    assert not called, "must not OCR a document that has a text layer"


def test_read_falls_back_to_ocr_only_when_there_is_no_text(monkeypatch, tmp_path):
    monkeypatch.setattr(pdf, "text_layer", lambda p, timeout=120: "  ")
    monkeypatch.setattr(pdf, "ocr", lambda *a, **k: (
        "scanned words", {"ocr": True, "human_verified": False,
                          "pages_ocred": 1, "page_cap": 50, "dpi": 300}))
    txt, prov = pdf.read(tmp_path / "f.pdf")
    assert prov["source"] == "ocr"
    assert prov["ocr"] is True and prov["human_verified"] is False


def test_no_ocr_reports_the_document_as_unread(monkeypatch, tmp_path):
    """Refusing to OCR must not look like an empty document."""
    monkeypatch.setattr(pdf, "text_layer", lambda p, timeout=120: "")
    txt, prov = pdf.read(tmp_path / "f.pdf", allow_ocr=False)
    assert prov["source"] == "none"
    assert prov["ocr"] is False


def test_remote_pdfs_are_decoded_not_grepped_as_binary(monkeypatch, tmp_path, capsys):
    """The PDF guard lived only on the LOCAL branch.

    `extract <pdf-url>` therefore grepped raw binary, matched nothing, and
    reported "100.0% reduction" -- a FALSE ABSENCE in the tool built to prevent
    them. The same file by local path found the passage. Worker-reported on a
    live Kane County records task.
    """
    import cascade_search.cli as cli
    from cascade_search.core import pdf as pdfmod

    pdf_bytes = b"%PDF-1.7\n" + b"binary" * 200

    def fake_fetch(url, **kw):
        return (pdf_bytes if kw.get("binary") else "%PDF-1.7\nmangled"), None

    monkeypatch.setattr(cli, "_fetch", fake_fetch, raising=False)
    import cascade_search.core.http as http
    monkeypatch.setattr(http, "fetch", fake_fetch)
    monkeypatch.setattr(pdfmod, "text_layer",
                        lambda p, timeout=120: "SEC. 11101. AUTHORIZATION " * 40)

    code = cli.main(["extract", "https://example.gov/x.pdf", "--grep", "11101"])
    out = capsys.readouterr().out
    assert code == 0
    assert "PDF text layer" in out, "remote PDF must report its decode source"
    assert "11101" in out, "must find the passage, not grep binary"


def test_remote_pdf_is_refetched_as_bytes(monkeypatch):
    """Re-encoding the decoded text corrupts the PDF -- enough that a file with
    a perfect text layer fell back to OCR. The bytes must come off the wire
    undecoded."""
    import inspect
    import cascade_search.cli as cli
    src = inspect.getsource(cli.main)
    assert "binary=True" in src, "remote PDF path must re-fetch as bytes"
