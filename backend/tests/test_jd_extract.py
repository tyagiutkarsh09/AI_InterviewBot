"""JD file extraction.

WHY: extraction must FAIL LOUD (raise) on unsupported files and on empty/garbage
text, so an admin never silently starts a JD-less interview (repo rule 9).
"""
import io

import pytest

from src.lib.jd_extract import extract_jd_text, JDExtractError


def _make_docx_bytes(text: str) -> bytes:
    from docx import Document

    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_docx_happy_path_returns_text():
    data = _make_docx_bytes("Senior Python Engineer\nBuild FastAPI services.")
    out = extract_jd_text("jd.docx", data)
    assert "Senior Python Engineer" in out
    assert "FastAPI" in out


def test_unsupported_extension_raises():
    with pytest.raises(JDExtractError):
        extract_jd_text("jd.txt", b"plain text")


def test_no_extension_raises():
    with pytest.raises(JDExtractError):
        extract_jd_text("jd", b"whatever")


def test_empty_docx_raises():
    data = _make_docx_bytes("   \n  \n")
    with pytest.raises(JDExtractError):
        extract_jd_text("jd.docx", data)


def test_pdf_extraction_wiring(monkeypatch):
    # Verify our PDF branch joins page text and strips, without a binary fixture.
    class _Page:
        def __init__(self, t):
            self._t = t

        def extract_text(self):
            return self._t

    class _Reader:
        def __init__(self, *_a, **_k):
            self.pages = [_Page("Backend Engineer "), _Page("Kafka, Postgres")]

    monkeypatch.setattr("src.lib.jd_extract.PdfReader", _Reader)
    out = extract_jd_text("jd.pdf", b"%PDF-fake")
    assert "Backend Engineer" in out
    assert "Kafka, Postgres" in out


def test_empty_pdf_raises(monkeypatch):
    class _Page:
        def extract_text(self):
            return ""

    class _Reader:
        def __init__(self, *_a, **_k):
            self.pages = [_Page()]

    monkeypatch.setattr("src.lib.jd_extract.PdfReader", _Reader)
    with pytest.raises(JDExtractError):
        extract_jd_text("jd.pdf", b"%PDF-fake")


def test_corrupt_pdf_wraps_as_jd_extract_error(monkeypatch):
    # A pypdf parse failure must surface as JDExtractError, not leak the raw
    # library exception — that wrapping is the non-obvious part of the design.
    def _bad_reader(*_a, **_k):
        raise ValueError("not a PDF")

    monkeypatch.setattr("src.lib.jd_extract.PdfReader", _bad_reader)
    with pytest.raises(JDExtractError):
        extract_jd_text("jd.pdf", b"garbage")
