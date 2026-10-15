"""
File System Tool — Sandboxed read/write file lokal.

Security model:
- HANYA bisa akses path yang ada di ALLOWED_DIRS whitelist
- Path traversal (../../etc/passwd) otomatis ditolak
- Write/delete adalah DESTRUCTIVE — butuh konfirmasi user
- Max file size: 1MB untuk read (hindari flood context window)
"""

import logging
import os
from pathlib import Path
from typing import Any

from app.tools.base import BaseTool, PermissionLevel, ToolParameter, ToolResult, ToolSchema, ToolStatus

logger = logging.getLogger(__name__)

# Direktori yang diizinkan — user konfigurasi ini di .env
# Default: folder Documents dan Desktop user
DEFAULT_ALLOWED_DIRS = [
    str(Path.home() / "Documents"),
    str(Path.home() / "Desktop"),
    str(Path.home() / "prometheus_workspace"),  # dedicated workspace
]

MAX_READ_SIZE = 1 * 1024 * 1024  # 1MB


def _is_path_allowed(path: Path, allowed_dirs: list[str]) -> bool:
    """
    Cek apakah path ada di dalam salah satu allowed_dirs.
    Resolve symlinks dan relative paths dulu untuk prevent traversal.
    """
    try:
        resolved = path.resolve()
        for allowed in allowed_dirs:
            allowed_path = Path(allowed).resolve()
            try:
                resolved.relative_to(allowed_path)
                return True
            except ValueError:
                continue
        return False
    except Exception:
        return False


class FileReadTool(BaseTool):
    """Baca konten file dari direktori yang diizinkan."""

    def __init__(self, allowed_dirs: list[str] | None = None):
        self._allowed_dirs = allowed_dirs or DEFAULT_ALLOWED_DIRS

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_read",
            description=(
                "Baca konten file dari file system lokal. "
                "Hanya bisa akses file di direktori yang sudah diizinkan. "
                "Gunakan ini untuk membaca kode, dokumentasi, atau file text lainnya."
            ),
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Path absolut atau relatif ke file yang ingin dibaca.",
                ),
                ToolParameter(
                    name="encoding",
                    type="string",
                    description="Encoding file (default: utf-8).",
                    required=False,
                    default="utf-8",
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        raw_path: str = kwargs["path"]
        encoding: str = kwargs.get("encoding", "utf-8")

        path = Path(raw_path)

        # Security check
        if not _is_path_allowed(path, self._allowed_dirs):
            return ToolResult(
                status=ToolStatus.DENIED,
                output=None,
                error=(
                    f"Akses ke '{raw_path}' ditolak. "
                    f"Hanya direktori berikut yang diizinkan: {self._allowed_dirs}"
                ),
            )

        if not path.exists():
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=f"File tidak ditemukan: {raw_path}",
            )

        if not path.is_file():
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=f"'{raw_path}' bukan file (mungkin direktori).",
            )

        # Size check
        size = path.stat().st_size
        if size > MAX_READ_SIZE:
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=f"File terlalu besar ({size / 1024:.1f}KB). Maksimum: {MAX_READ_SIZE / 1024:.0f}KB.",
            )

        try:
            content = path.read_text(encoding=encoding)
            return ToolResult(
                status=ToolStatus.SUCCESS,
                output=content,
                metadata={
                    "path": str(path.resolve()),
                    "size_bytes": size,
                    "encoding": encoding,
                },
            )
        except UnicodeDecodeError:
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=f"Tidak bisa decode file dengan encoding '{encoding}'. Coba encoding lain.",
            )
        except Exception as e:
            logger.exception(f"file_read failed: {raw_path}")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class FileWriteTool(BaseTool):
    """Tulis konten ke file. DESTRUCTIVE — butuh konfirmasi."""

    def __init__(self, allowed_dirs: list[str] | None = None):
        self._allowed_dirs = allowed_dirs or DEFAULT_ALLOWED_DIRS

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_write",
            description=(
                "Tulis konten ke file di file system lokal. "
                "⚠️ Akan OVERWRITE file yang sudah ada. "
                "Hanya bisa akses direktori yang sudah diizinkan."
            ),
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Path file yang ingin ditulis.",
                ),
                ToolParameter(
                    name="content",
                    type="string",
                    description="Konten yang akan ditulis ke file.",
                ),
                ToolParameter(
                    name="encoding",
                    type="string",
                    description="Encoding file (default: utf-8).",
                    required=False,
                    default="utf-8",
                ),
                ToolParameter(
                    name="create_dirs",
                    type="boolean",
                    description="Buat direktori parent jika belum ada (default: false).",
                    required=False,
                    default=False,
                ),
            ],
            permission=PermissionLevel.DESTRUCTIVE,
            requires_confirmation=True,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        raw_path: str = kwargs["path"]
        content: str = kwargs["content"]
        encoding: str = kwargs.get("encoding", "utf-8")
        create_dirs: bool = kwargs.get("create_dirs", False)

        path = Path(raw_path)

        if not _is_path_allowed(path, self._allowed_dirs):
            return ToolResult(
                status=ToolStatus.DENIED,
                output=None,
                error=f"Akses write ke '{raw_path}' ditolak.",
            )

        try:
            if create_dirs:
                path.parent.mkdir(parents=True, exist_ok=True)

            existed = path.exists()
            path.write_text(content, encoding=encoding)

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output={
                    "path": str(path.resolve()),
                    "bytes_written": len(content.encode(encoding)),
                    "action": "overwritten" if existed else "created",
                },
            )
        except Exception as e:
            logger.exception(f"file_write failed: {raw_path}")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class FileListTool(BaseTool):
    """List isi direktori."""

    def __init__(self, allowed_dirs: list[str] | None = None):
        self._allowed_dirs = allowed_dirs or DEFAULT_ALLOWED_DIRS

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="file_list",
            description=(
                "List isi direktori. Tampilkan file dan subdirektori "
                "beserta ukuran dan tanggal modifikasi."
            ),
            parameters=[
                ToolParameter(
                    name="path",
                    type="string",
                    description="Path direktori yang ingin di-list.",
                ),
                ToolParameter(
                    name="pattern",
                    type="string",
                    description="Glob pattern untuk filter (contoh: '*.py', '*.md'). Default: '*' (semua).",
                    required=False,
                    default="*",
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        raw_path: str = kwargs["path"]
        pattern: str = kwargs.get("pattern", "*")

        path = Path(raw_path)

        if not _is_path_allowed(path, self._allowed_dirs):
            return ToolResult(
                status=ToolStatus.DENIED,
                output=None,
                error=f"Akses ke '{raw_path}' ditolak.",
            )

        if not path.exists():
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=f"Direktori tidak ditemukan: {raw_path}",
            )

        if not path.is_dir():
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=f"'{raw_path}' bukan direktori.",
            )

        try:
            entries = []
            for entry in sorted(path.glob(pattern)):
                stat = entry.stat()
                entries.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                    "size_bytes": stat.st_size if entry.is_file() else None,
                    "modified": stat.st_mtime,
                })

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output={
                    "path": str(path.resolve()),
                    "count": len(entries),
                    "entries": entries,
                },
            )
        except Exception as e:
            logger.exception(f"file_list failed: {raw_path}")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))