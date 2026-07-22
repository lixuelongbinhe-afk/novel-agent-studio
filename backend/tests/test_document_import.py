from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfWriter

from app.services import document_import


def _docx_bytes(text: str) -> bytes:
    document = Document()
    document.add_paragraph(text)
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _pdf_bytes(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=300)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_docx_is_parsed_inside_budget() -> None:
    text = document_import.extract_document_text(_docx_bytes("第一章 风起"), "draft.docx")
    assert text == "第一章 风起"


def test_docx_abnormal_compression_ratio_is_rejected_before_parsing() -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "A" * 2_000_000)

    with pytest.raises(document_import.DocumentImportError) as exc_info:
        document_import.extract_document_text(buffer.getvalue(), "bomb.docx")

    assert exc_info.value.status_code == 413
    assert "压缩比" in exc_info.value.detail


def test_pdf_page_budget_is_enforced_in_worker() -> None:
    limits = document_import.ImportLimits(pdf_max_pages=2)
    with pytest.raises(document_import.DocumentImportError) as exc_info:
        document_import.extract_document_text(
            _pdf_bytes(3), "too-many-pages.pdf", limits=limits
        )

    assert exc_info.value.status_code == 413
    assert "2 页" in exc_info.value.detail


def test_parser_worker_is_terminated_after_timeout() -> None:
    limits = document_import.ImportLimits(parse_timeout_seconds=0.001)
    with pytest.raises(document_import.DocumentImportError) as exc_info:
        document_import.extract_document_text(
            _pdf_bytes(1), "slow.pdf", limits=limits
        )

    assert exc_info.value.status_code == 504


def test_extracted_text_budget_is_enforced() -> None:
    limits = document_import.ImportLimits(max_text_chars=10)
    with pytest.raises(document_import.DocumentImportError) as exc_info:
        document_import.extract_document_text(
            "这是一段超过十个字符的正文内容".encode(), "draft.txt", limits=limits
        )

    assert exc_info.value.status_code == 413
