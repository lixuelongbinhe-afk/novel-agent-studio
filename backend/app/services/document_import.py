from __future__ import annotations

import asyncio
import multiprocessing
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from docx import Document
from pypdf import PdfReader


@dataclass(frozen=True)
class ImportLimits:
    max_upload_bytes: int = 10 * 1024 * 1024
    max_text_chars: int = 5_000_000
    parse_timeout_seconds: float = 20.0
    docx_max_entries: int = 2_048
    docx_max_expanded_bytes: int = 64 * 1024 * 1024
    docx_max_member_bytes: int = 32 * 1024 * 1024
    docx_max_compression_ratio: float = 200.0
    pdf_max_pages: int = 500


class DocumentImportError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def extract_document_text(
    content: bytes,
    filename: str,
    *,
    allowed: set[str] | None = None,
    limits: ImportLimits | None = None,
) -> str:
    active_limits = limits or ImportLimits()
    suffix = _suffix(filename)
    accepted = allowed or {".txt", ".md", ".markdown", ".docx", ".pdf"}
    if suffix not in accepted:
        formats = "、".join(sorted(item.removeprefix(".").upper() for item in accepted))
        raise DocumentImportError(415, f"仅支持 {formats} 文件")
    if len(content) > active_limits.max_upload_bytes:
        raise DocumentImportError(
            413,
            f"文件不得超过 {active_limits.max_upload_bytes // (1024 * 1024)} MB",
        )

    if suffix in {".txt", ".md", ".markdown"}:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise DocumentImportError(422, "文本文件必须使用 UTF-8 编码") from exc
        return _validate_text(text, active_limits)

    if suffix == ".docx":
        _validate_docx_archive(content, active_limits)
    return _parse_in_worker(content, suffix, active_limits)


async def extract_document_text_async(
    content: bytes,
    filename: str,
    *,
    allowed: set[str] | None = None,
    limits: ImportLimits | None = None,
) -> str:
    return await asyncio.to_thread(
        extract_document_text,
        content,
        filename,
        allowed=allowed,
        limits=limits,
    )


def _suffix(filename: str) -> str:
    normalized = filename.replace("\\", "/")
    leaf = PurePosixPath(normalized).name
    dot = leaf.rfind(".")
    return leaf[dot:].lower() if dot >= 0 else ""


def _validate_docx_archive(content: bytes, limits: ImportLimits) -> None:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > limits.docx_max_entries:
                raise DocumentImportError(413, "Word 文件内部条目过多")
            expanded = 0
            for entry in entries:
                path = PurePosixPath(entry.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts:
                    raise DocumentImportError(422, "Word 文件包含不安全的内部路径")
                if entry.flag_bits & 0x1:
                    raise DocumentImportError(422, "不支持加密的 Word 文件")
                if entry.file_size > limits.docx_max_member_bytes:
                    raise DocumentImportError(413, "Word 文件中的单个资源过大")
                expanded += entry.file_size
                if expanded > limits.docx_max_expanded_bytes:
                    raise DocumentImportError(413, "Word 文件解压后的内容过大")
                compressed = max(entry.compress_size, 1)
                if entry.file_size / compressed > limits.docx_max_compression_ratio:
                    raise DocumentImportError(413, "Word 文件压缩比异常，已拒绝解析")
    except DocumentImportError:
        raise
    except (zipfile.BadZipFile, OSError, ValueError) as exc:
        raise DocumentImportError(422, "Word 文件损坏或格式不受支持") from exc


def _parse_in_worker(content: bytes, suffix: str, limits: ImportLimits) -> str:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(
        target=_document_parser_worker,
        args=(child, content, suffix, limits),
        daemon=True,
    )
    process.start()
    child.close()
    try:
        if not parent.poll(limits.parse_timeout_seconds):
            process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(5)
            raise DocumentImportError(504, "文件解析超时，请缩小文件后重试")
        kind, payload = parent.recv()
    except EOFError as exc:
        raise DocumentImportError(422, "文件解析进程异常退出") from exc
    finally:
        parent.close()
        if process.is_alive():
            process.join(1)
        if process.is_alive():
            process.terminate()
            process.join(5)
        process.close()
    if kind == "ok":
        return str(payload)
    status_code, detail = payload
    raise DocumentImportError(int(status_code), str(detail))


def _document_parser_worker(
    connection: multiprocessing.connection.Connection,
    content: bytes,
    suffix: str,
    limits: ImportLimits,
) -> None:
    try:
        if suffix == ".docx":
            document = Document(BytesIO(content))
            text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        elif suffix == ".pdf":
            reader = PdfReader(BytesIO(content))
            if reader.is_encrypted:
                raise DocumentImportError(422, "不支持加密的 PDF 文件")
            if len(reader.pages) > limits.pdf_max_pages:
                raise DocumentImportError(413, f"PDF 不得超过 {limits.pdf_max_pages} 页")
            blocks: list[str] = []
            total = 0
            for page in reader.pages:
                block = page.extract_text() or ""
                total += len(block)
                if total > limits.max_text_chars:
                    raise DocumentImportError(413, "文件提取出的文本过长")
                blocks.append(block)
            text = "\n\n".join(blocks)
        else:
            raise DocumentImportError(415, "不支持的文件格式")
        connection.send(("ok", _validate_text(text, limits)))
    except DocumentImportError as exc:
        connection.send(("error", (exc.status_code, exc.detail)))
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile):
        connection.send(("error", (422, "文件损坏、加密或格式不受支持")))
    except Exception:
        connection.send(("error", (422, "文件解析失败")))
    finally:
        connection.close()


def _validate_text(text: str, limits: ImportLimits) -> str:
    if len(text) > limits.max_text_chars:
        raise DocumentImportError(413, "文件提取出的文本过长")
    normalized = re.sub(r"\x00", "", text).strip()
    if not normalized:
        raise DocumentImportError(422, "文件中没有可识别的正文文本")
    return normalized
