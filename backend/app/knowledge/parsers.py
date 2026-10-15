"""
Document Parsers — Extract text dari berbagai format file (Wave 1).

Wave 1: PDF, markdown, txt, code files.
Wave 2 (nanti): Word (.docx), Excel (.xlsx).

Design: tiap parser return text + metadata. Metadata bisa beda per format
(PDF punya page number, code punya language). Downstream (chunker) treat
semua sama — cuma butuh text + base metadata.

Semua parser return format seragam:
    ParseResult(text=str, metadata=dict, pages=list[PageContent] | None)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Ekstensi file yang dianggap "code" — di-parse sebagai plain text
# tapi metadata language di-tag untuk konteks.
CODE_EXTENSIONS = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".jsx": "javascript", ".java": "java",
    ".go": "go", ".rs": "rust", ".c": "c", ".cpp": "cpp",
    ".h": "c", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby",
    ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".sql": "sql", ".sh": "bash", ".yaml": "yaml", ".yml": "yaml",
    ".json": "json", ".html": "html", ".css": "css", ".vue": "vue",
}

TEXT_EXTENSIONS = {".txt", ".text", ".log", ".env", ".ini", ".cfg", ".toml"}
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdx"}
PDF_EXTENSIONS = {".pdf"}


@dataclass
class PageContent:
    """Konten satu halaman (untuk PDF yang punya konsep halaman)."""
    page_number: int
    text: str


@dataclass
class ParseResult:
    """Hasil parsing dokumen — seragam untuk semua format."""
    text: str                                    # full text
    metadata: dict = field(default_factory=dict)  # source_type, language, dll
    pages: list[PageContent] | None = None       # None kalau format tidak punya halaman


class ParserError(Exception):
    """Raised kalau parsing gagal."""
    pass


def detect_source_type(filename: str) -> str:
    """Deteksi tipe dokumen dari ekstensi file."""
    ext = Path(filename).suffix.lower()
    if ext in PDF_EXTENSIONS:
        return "pdf"
    if ext in MARKDOWN_EXTENSIONS:
        return "markdown"
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in TEXT_EXTENSIONS:
        return "text"
    # Default: coba treat sebagai text
    return "text"


def is_supported(filename: str) -> bool:
    """Cek apakah file didukung di Wave 1."""
    ext = Path(filename).suffix.lower()
    return ext in (
        PDF_EXTENSIONS | MARKDOWN_EXTENSIONS | set(CODE_EXTENSIONS.keys()) | TEXT_EXTENSIONS
    )


# ── Parsers ───────────────────────────────────────────────────────────────────

def parse_text(content: bytes, filename: str) -> ParseResult:
    """Parse plain text / markdown / code — decode bytes jadi string."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        # Fallback ke latin-1 kalau bukan UTF-8
        text = content.decode("latin-1", errors="replace")

    ext = Path(filename).suffix.lower()
    source_type = detect_source_type(filename)

    metadata = {
        "source_type": source_type,
        "filename": filename,
    }

    # Tag language untuk code files
    if source_type == "code":
        metadata["language"] = CODE_EXTENSIONS.get(ext, "unknown")

    return ParseResult(text=text, metadata=metadata)


def parse_pdf(content: bytes, filename: str) -> ParseResult:
    """
    Parse PDF pakai pymupdf (fitz). Extract text per halaman.

    Kenapa pymupdf bukan pypdf?
    - pymupdf jauh lebih akurat extract text (terutama layout kompleks)
    - Lebih cepat
    - Handle lebih banyak edge case (encrypted, malformed)
    """
    try:
        import fitz  # pymupdf
    except ImportError:
        raise ParserError(
            "pymupdf belum terinstall. Jalankan: pip install pymupdf"
        )

    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as e:
        raise ParserError(f"Gagal buka PDF: {e}")

    pages: list[PageContent] = []
    full_text_parts: list[str] = []

    try:
        for page_num, page in enumerate(doc, start=1):
            page_text = page.get_text().strip()
            if page_text:
                pages.append(PageContent(page_number=page_num, text=page_text))
                full_text_parts.append(page_text)
    finally:
        doc.close()

    if not full_text_parts:
        raise ParserError(
            "PDF tidak mengandung text yang bisa di-extract. "
            "Mungkin ini PDF hasil scan (perlu OCR — belum didukung di Wave 1)."
        )

    return ParseResult(
        text="\n\n".join(full_text_parts),
        metadata={
            "source_type": "pdf",
            "filename": filename,
            "page_count": len(pages),
        },
        pages=pages,
    )


def parse_document(content: bytes, filename: str) -> ParseResult:
    """
    Entry point utama — route ke parser yang tepat berdasarkan ekstensi.

    Args:
        content: raw bytes dari file
        filename: nama file (untuk deteksi tipe)

    Returns:
        ParseResult dengan text + metadata

    Raises:
        ParserError kalau format tidak didukung atau parsing gagal
    """
    if not is_supported(filename):
        ext = Path(filename).suffix.lower()
        raise ParserError(
            f"Format '{ext}' belum didukung di Wave 1. "
            f"Didukung: PDF, markdown, txt, code files. "
            f"(Word/Excel menyusul di Wave 2)"
        )

    source_type = detect_source_type(filename)

    if source_type == "pdf":
        return parse_pdf(content, filename)
    else:
        # markdown, text, code — semua plain text
        return parse_text(content, filename)