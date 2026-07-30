"""Tests for document parser — PDF/TXT/MD/DOCX text extraction."""

from pathlib import Path

import pytest

from gpcg.infrastructure.document_parser import DocumentParseError, detect_type, parse_document


class TestDocumentParser:
    def test_parse_txt(self, tmp_path: Path):
        p = tmp_path / "test.txt"
        p.write_text("Hello world\nLine 2")
        text = parse_document(p)
        assert "Hello world" in text
        assert "Line 2" in text

    def test_parse_md(self, tmp_path: Path):
        p = tmp_path / "test.md"
        p.write_text("# Title\n\nSome **markdown** text.")
        text = parse_document(p)
        assert "Title" in text
        assert "markdown" in text

    def test_detect_type(self):
        assert detect_type("doc.pdf") == "pdf"
        assert detect_type("doc.txt") == "txt"
        assert detect_type("doc.md") == "md"
        assert detect_type("doc.docx") == "docx"
        assert detect_type("doc.markdown") == "md"

    def test_detect_type_unsupported(self):
        with pytest.raises(DocumentParseError):
            detect_type("doc.xlsx")

    def test_parse_missing_file(self, tmp_path: Path):
        with pytest.raises(DocumentParseError):
            parse_document(tmp_path / "nonexistent.txt")
