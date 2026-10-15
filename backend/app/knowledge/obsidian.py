"""
Obsidian Sync — baca vault markdown lokal ke knowledge base (Phase 3d).

Vault Obsidian hanyalah folder berisi berkas .md. Tidak ada API, tidak ada
OAuth — cukup baca dari disk. Karena itu sumber ini yang paling sederhana
di antara semua konektor.

Yang membedakannya dari unggahan berkas: catatan berubah terus. Kalau setiap
sinkron memproses ulang seluruh vault, biayanya besar — terutama karena
embedding di mesin lokal lambat. Jadi sinkronisasi di sini INKREMENTAL:
hanya berkas yang berubah sejak sinkron terakhir yang diproses ulang.

Perbandingannya memakai waktu ubah berkas (mtime) lawan updated_at dokumen.
Sederhana, tidak perlu kolom baru, dan cukup akurat untuk catatan pribadi.
"""

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.chunker import FixedSizeChunker
from app.memory.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

NAMESPACE = "obsidian"

# Folder yang tidak pernah berisi catatan pengguna.
#
# Selain folder sistem Obsidian, ini juga menyaring bawaan proyek: paket
# Python dan Node membawa banyak README/CHANGELOG/SKILL.md. Kalau ikut
# terserap, knowledge base akan penuh dokumentasi pustaka pihak ketiga dan
# pencarian jadi berisik — pengguna bertanya soal catatannya, yang muncul
# malah potongan dokumen Playwright.
SKIP_DIRS = {
    # Obsidian & sistem
    ".obsidian", ".trash", ".DS_Store",
    # Kontrol versi
    ".git", ".svn", ".hg",
    # Dependensi
    "node_modules", "site-packages", "dist-packages", "vendor", "bower_components",
    # Lingkungan Python
    ".venv", "venv", "env", ".env", "__pycache__", ".tox", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "egg-info",
    # Hasil build & cache
    "dist", "build", ".next", ".nuxt", "out", "target", "coverage", ".cache",
}

# Batas ukuran satu catatan. Berkas di atas ini hampir pasti bukan catatan
# (lampiran yang salah ekstensi, dump data), dan memprosesnya merugikan.
MAX_NOTE_BYTES = 2 * 1024 * 1024


def vault_path() -> Path | None:
    """Lokasi vault dari .env, kalau diatur."""
    raw = os.getenv("OBSIDIAN_VAULT_PATH", "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    return p if p.is_dir() else None


def _iter_notes(root: Path):
    """Telusuri seluruh berkas markdown, lewati folder sistem Obsidian."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Ubah dirnames di tempat agar os.walk tidak masuk ke folder itu
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and not d.startswith(".")
            and not d.endswith((".dist-info", ".egg-info"))
        ]
        for name in filenames:
            if name.lower().endswith((".md", ".markdown")):
                yield Path(dirpath) / name


def _note_title(path: Path, root: Path) -> str:
    """
    Judul catatan = jalur relatifnya di dalam vault.

    Nama berkas saja sering ambigu ("Index.md" bisa ada di banyak folder),
    sedangkan jalur relatif langsung memberi konteks: "Proyek/Prometheus/Index".
    """
    rel = path.relative_to(root).with_suffix("")
    return str(rel).replace(os.sep, "/")[:200]


class ObsidianSync:
    """
    Sinkronkan vault Obsidian ke knowledge base.

    Usage:
        sync = ObsidianSync(db, embedding_service)
        hasil = await sync.run()
    """

    def __init__(
        self,
        db: AsyncSession,
        embedding_service: EmbeddingService,
        chunker: FixedSizeChunker | None = None,
    ):
        self._db = db
        self._embed_svc = embedding_service
        self._chunker = chunker or FixedSizeChunker()

    async def run(self, prune: bool = False) -> dict:
        """
        Jalankan sinkronisasi.

        Args:
            prune: hapus dokumen yang berkasnya sudah tidak ada di vault.
                   Mati secara bawaan — menghapus itu tidak bisa dibatalkan,
                   jadi pengguna harus memintanya secara sadar.
        """
        root = vault_path()
        if root is None:
            return {
                "status": "failed",
                "error": (
                    "OBSIDIAN_VAULT_PATH belum diatur di .env, atau foldernya "
                    "tidak ditemukan."
                ),
                "added": 0, "updated": 0, "skipped": 0, "failed": 0, "removed": 0,
            }

        # Catatan yang sudah tersimpan sebelumnya, untuk perbandingan
        existing = await self._db.execute(
            sql_text("""
                SELECT id, source_uri, updated_at FROM documents
                WHERE namespace = :ns
            """),
            {"ns": NAMESPACE},
        )
        known = {
            r.source_uri: {"id": str(r.id), "updated_at": r.updated_at}
            for r in existing.fetchall()
        }
        seen: set[str] = set()

        added = updated = skipped = failed = 0
        errors: list[str] = []

        for note in _iter_notes(root):
            uri = str(note)
            seen.add(uri)

            try:
                stat = note.stat()
                if stat.st_size > MAX_NOTE_BYTES:
                    skipped += 1
                    continue

                mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                prev = known.get(uri)

                # Belum berubah sejak terakhir disimpan — lewati
                if prev and prev["updated_at"] and mtime <= prev["updated_at"]:
                    skipped += 1
                    continue

                body = note.read_text(encoding="utf-8", errors="replace")
                if not body.strip():
                    skipped += 1
                    continue

                title = _note_title(note, root)

                if prev:
                    # Catatan berubah: buang potongan lama sebelum menulis ulang,
                    # kalau tidak isi lama dan baru akan bercampur saat dicari.
                    await self._db.execute(
                        sql_text("DELETE FROM document_chunks WHERE document_id = :id"),
                        {"id": prev["id"]},
                    )
                    doc_id = prev["id"]
                    await self._db.execute(
                        sql_text("""
                            UPDATE documents
                            SET title = :title, status = 'processing', updated_at = NOW()
                            WHERE id = :id
                        """),
                        {"title": title, "id": doc_id},
                    )
                    updated += 1
                else:
                    doc_id = str(uuid.uuid4())
                    await self._db.execute(
                        sql_text("""
                            INSERT INTO documents
                                (id, title, source_type, source_uri, namespace,
                                 status, chunk_count, created_at, updated_at)
                            VALUES (:id, :title, 'obsidian', :uri, :ns,
                                    'processing', 0, NOW(), NOW())
                        """),
                        {"id": doc_id, "title": title, "uri": uri, "ns": NAMESPACE},
                    )
                    added += 1

                await self._ingest(doc_id, title, uri, body)
                await self._db.commit()

            except Exception as e:
                failed += 1
                errors.append(f"{note.name}: {e}")
                logger.warning(f"Obsidian sync gagal untuk {note}: {e}")
                await self._db.rollback()

        removed = 0
        if prune:
            gone = [uri for uri in known if uri not in seen]
            for uri in gone:
                await self._db.execute(
                    sql_text("DELETE FROM documents WHERE id = :id"),
                    {"id": known[uri]["id"]},
                )
                removed += 1
            if gone:
                await self._db.commit()

        logger.info(
            f"Obsidian sync: +{added} baru, {updated} diperbarui, "
            f"{skipped} dilewati, {failed} gagal, {removed} dihapus"
        )
        return {
            "status": "ok",
            "error": None,
            "vault": str(root),
            "added": added,
            "updated": updated,
            "skipped": skipped,
            "failed": failed,
            "removed": removed,
            "errors": errors[:10],
        }

    async def _ingest(self, doc_id: str, title: str, uri: str, body: str) -> None:
        import json

        chunks = self._chunker.chunk(
            body,
            base_metadata={"source_type": "obsidian", "source_uri": uri, "title": title},
        )
        if not chunks:
            await self._db.execute(
                sql_text("""
                    UPDATE documents SET status = 'ready', chunk_count = 0, updated_at = NOW()
                    WHERE id = :id
                """),
                {"id": doc_id},
            )
            return

        # Kalau satu potongan gagal di-embed, jangan jatuhkan seluruh catatan.
        # Lebih baik menyimpan sembilan dari sepuluh potongan daripada nol.
        pasangan: list[tuple] = []
        try:
            embeddings = await self._embed_svc.embed_many([c.content for c in chunks])
            pasangan = list(zip(chunks, embeddings))
        except Exception:
            for chunk in chunks:
                try:
                    pasangan.append((chunk, await self._embed_svc.embed(chunk.content)))
                except Exception as e:
                    logger.warning(f"Potongan {chunk.index} '{title}' dilewati: {e}")

        if not pasangan:
            raise RuntimeError("Tidak ada potongan yang berhasil di-embed.")

        for chunk, emb in pasangan:
            await self._db.execute(
                sql_text("""
                    INSERT INTO document_chunks
                        (id, document_id, content, embedding, chunk_index, metadata, created_at)
                    VALUES (gen_random_uuid(), :doc_id, :content,
                            CAST(:embedding AS vector), :idx, CAST(:meta AS jsonb), NOW())
                """),
                {
                    "doc_id": doc_id, "content": chunk.content,
                    "embedding": str(emb), "idx": chunk.index,
                    "meta": json.dumps(chunk.metadata),
                },
            )

        await self._db.execute(
            sql_text("""
                UPDATE documents SET status = 'ready', chunk_count = :n, updated_at = NOW()
                WHERE id = :id
            """),
            {"n": len(chunks), "id": doc_id},
        )