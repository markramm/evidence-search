"""PDF text, with OCR as an explicit last resort.

Most documents on this beat -- court filings, procurement records, the Federal
Register -- carry a real text layer. `pdftotext` returns it exactly, in
milliseconds. OCR is slower and LOSSY in the one way that matters here: this
work runs on exact strings (`15JA5426P00000137`, `5:21-cv-02497-EJD`,
`$313,769,023`), and Tesseract confuses 0/O, 1/l, 5/S and drops digits in
tables. A silently OCR'd docket number that is one character wrong is exactly
the confident-wrong-answer class this package exists to prevent -- and it would
be archived with a SHA-256, looking authoritative.

So the rule is: extract the text layer. Fall back to OCR ONLY when there is no
text layer to lose -- a scanned document -- and when that happens, say so
loudly and mark every downstream artifact `ocr: true, human_verified: false`.

The scanned case is real, not hypothetical: the corpus records scanned Illinois
State Police PDFs as a live blocker.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

#: Below this many characters of extracted text, a PDF is treated as scanned.
#: A text-layer PDF yields thousands; a scanned one yields a handful of stray
#: glyphs from cover pages or stamps, not zero.
TEXT_LAYER_MIN_CHARS = 200


class PdfToolMissing(RuntimeError):
    """A required binary is not installed."""


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def text_layer(path: Path, timeout: int = 120) -> str:
    """Extract the embedded text layer. Exact, fast, lossless."""
    if not have("pdftotext"):
        raise PdfToolMissing(
            "pdftotext not found. Install poppler: brew install poppler")
    try:
        r = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                           capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ""
    return r.stdout.decode("utf-8", errors="replace")


def ocr(path: Path, max_pages: int = 50, dpi: int = 300,
        timeout: int = 900) -> tuple[str, dict]:
    """Rasterise and OCR. Returns (text, provenance).

    Capped at `max_pages` because OCR is slow and a worker waiting ten minutes
    on a 400-page exhibit is a worse outcome than a partial read it knows is
    partial. The cap is reported, never silent.
    """
    for tool in ("pdftoppm", "tesseract"):
        if not have(tool):
            raise PdfToolMissing(
                f"{tool} not found. Install: brew install poppler tesseract")

    pages_done = 0
    chunks: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        try:
            subprocess.run(
                ["pdftoppm", "-r", str(dpi), "-png", "-l", str(max_pages),
                 str(path), str(tmp / "pg")],
                capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            pass
        for img in sorted(tmp.glob("pg*.png")):
            try:
                r = subprocess.run(["tesseract", str(img), "-", "-l", "eng"],
                                   capture_output=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                continue
            chunks.append(r.stdout.decode("utf-8", errors="replace"))
            pages_done += 1

    return "\n".join(chunks), {
        "ocr": True,
        "human_verified": False,
        "engine": "tesseract",
        "dpi": dpi,
        "pages_ocred": pages_done,
        "page_cap": max_pages,
        "truncated": pages_done >= max_pages,
        "caution": ("OCR text is NOT verbatim. Confirm every identifier, docket "
                    "number, and dollar figure against the page image before "
                    "citing -- 0/O, 1/l and 5/S are routinely confused."),
    }


def read(path: Path, allow_ocr: bool = True, max_pages: int = 50):
    """Return (text, provenance).

    provenance['source'] is 'text-layer' or 'ocr'. When it is 'ocr', the text
    was never human-verified and every artifact derived from it must say so.
    """
    txt = text_layer(path)
    if len(txt.strip()) >= TEXT_LAYER_MIN_CHARS:
        return txt, {"source": "text-layer", "ocr": False, "human_verified": False,
                     "chars": len(txt)}

    prov = {"source": "none", "ocr": False, "human_verified": False,
            "text_layer_chars": len(txt.strip())}
    if not allow_ocr:
        return txt, prov

    ocr_txt, ocr_prov = ocr(path, max_pages=max_pages)
    prov.update(ocr_prov)
    prov["source"] = "ocr"
    return ocr_txt, prov
