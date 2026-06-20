"""Extract plain text from an uploaded JD file (PDF or DOCX).

Fails loud (raises JDExtractError) on unsupported extension, unreadable bytes, or
empty/whitespace-only extracted text — never returns "". A silently empty JD would
produce a JD-less interview, which defeats the feature.
"""
import io
import logging
import os

from pypdf import PdfReader

logger = logging.getLogger(__name__)


class JDExtractError(RuntimeError):
    """Raised when a JD file cannot be turned into usable text."""


def _extract_pdf(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _extract_docx(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs)


def extract_jd_text(filename: str | None, data: bytes) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    try:
        if ext == ".pdf":
            text = _extract_pdf(data)
        elif ext == ".docx":
            text = _extract_docx(data)
        else:
            raise JDExtractError(f"Unsupported JD file type: {ext or '(none)'}")
    except JDExtractError:
        raise
    except Exception as exc:
        # Caller turns JDExtractError into an HTTP 4xx, so this is expected/recoverable.
        logger.warning("JD extraction failed for %s: %s", filename, exc)
        raise JDExtractError(f"Could not read JD file: {exc}") from exc

    # Empty-text guard lives at the call boundary (not in the helpers) so a PDF of
    # image-only pages and a whitespace-only DOCX are both caught in one place.
    if not text.strip():
        raise JDExtractError("JD file produced no extractable text")
    return text.strip()
