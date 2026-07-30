"""Document parser — extract text from PDF, TXT, MD, DOCX uploads."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class DocumentParseError(Exception):
    pass


def detect_type(filename: str) -> str:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext in ("pdf", "txt", "md", "markdown", "docx", "doc"):
        return "md" if ext == "markdown" else ext
    raise DocumentParseError(f"unsupported file type: .{ext}")


def parse_document(path: str | Path, file_type: Optional[str] = None) -> str:
    """Extract plain text from a document. Returns the text content."""
    path = Path(path)
    if not path.exists():
        raise DocumentParseError(f"file not found: {path}")
    ft = file_type or detect_type(path.name)

    if ft in ("txt", "md"):
        return path.read_text(encoding="utf-8", errors="replace")

    if ft == "pdf":
        try:
            from pypdf import PdfReader
        except ImportError as e:
            raise DocumentParseError("pypdf not installed") from e
        reader = PdfReader(str(path))
        parts = []
        for i, page in enumerate(reader.pages):
            try:
                txt = page.extract_text() or ""
            except Exception:
                txt = ""
            if txt.strip():
                parts.append(f"--- Page {i + 1} ---\n{txt}")
        return "\n\n".join(parts)

    if ft in ("docx", "doc"):
        try:
            from docx import Document
        except ImportError as e:
            raise DocumentParseError("python-docx not installed") from e
        doc = Document(str(path))
        parts = []
        for para in doc.paragraphs:
            if para.text.strip():
                parts.append(para.text)
        return "\n\n".join(parts)

    raise DocumentParseError(f"unsupported file type: {ft}")
