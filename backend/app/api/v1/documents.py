"""
Documents API — Upload dan management knowledge base documents.

Endpoints:
- POST   /documents/upload    → upload 1+ file (multi-file), ingest ke KB
- GET    /documents           → list semua dokumen + status
- GET    /documents/{id}      → detail dokumen + chunks
- DELETE /documents/{id}      → hapus dokumen + chunks (cascade)

Upload flow: file diterima → IngestionService (parse→chunk→embed→store).
Multi-file: tiap file di-ingest terpisah, return hasil per-file (biar kalau
1 file gagal, yang lain tetep jalan).
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select, text as sql_text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.middleware import AuthDep
from app.core.database import get_db_session
from app.core.dependencies import get_embedding_service
from app.core.models import Document, DocumentChunk
from app.knowledge.clipper import ClipperService
from app.knowledge.obsidian import ObsidianSync, vault_path
from app.knowledge.ingestion import IngestionService
from app.knowledge.parsers import is_supported
from app.memory.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["documents"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]
EmbedDep = Annotated[EmbeddingService, Depends(get_embedding_service)]

# Batas ukuran file (10 MB) — cegah upload file kegedean
MAX_FILE_SIZE = 10 * 1024 * 1024


# ── Schemas ───────────────────────────────────────────────────────────────────

class DocumentOut(BaseModel):
    id: str
    title: str
    source_type: str
    namespace: str | None
    status: str
    chunk_count: int
    error_message: str | None
    created_at: str


class UploadResultItem(BaseModel):
    filename: str
    document_id: str | None
    status: str          # ready | failed
    chunk_count: int
    error: str | None


class UploadResponse(BaseModel):
    results: list[UploadResultItem]
    total_uploaded: int
    total_failed: int


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/upload", response_model=UploadResponse)
async def upload_documents(
    _: AuthDep,
    db: DbDep,
    embedding_svc: EmbedDep,
    files: list[UploadFile] = File(...),
    namespace: str | None = Form(default=None),
):
    """
    Upload 1+ dokumen ke knowledge base (multi-file).

    Tiap file di-ingest terpisah. Kalau satu gagal, yang lain tetap diproses.
    """
    if not files:
        raise HTTPException(status_code=400, detail="Tidak ada file yang di-upload.")

    service = IngestionService(db=db, embedding_service=embedding_svc)
    results: list[UploadResultItem] = []

    for upload in files:
        filename = upload.filename or "untitled"

        # Validasi format
        if not is_supported(filename):
            results.append(UploadResultItem(
                filename=filename,
                document_id=None,
                status="failed",
                chunk_count=0,
                error=f"Format tidak didukung (Wave 1: PDF, markdown, txt, code).",
            ))
            continue

        # Baca content + validasi ukuran
        content = await upload.read()
        if len(content) > MAX_FILE_SIZE:
            results.append(UploadResultItem(
                filename=filename,
                document_id=None,
                status="failed",
                chunk_count=0,
                error=f"File terlalu besar (max {MAX_FILE_SIZE // 1024 // 1024} MB).",
            ))
            continue

        # Ingest
        result = await service.ingest(
            content=content,
            filename=filename,
            namespace=namespace,
        )
        results.append(UploadResultItem(
            filename=filename,
            document_id=result["document_id"],
            status=result["status"],
            chunk_count=result["chunk_count"],
            error=result["error"],
        ))

    total_ok = sum(1 for r in results if r.status == "ready")
    total_failed = sum(1 for r in results if r.status == "failed")

    return UploadResponse(
        results=results,
        total_uploaded=total_ok,
        total_failed=total_failed,
    )


class ClipRequest(BaseModel):
    url: str
    namespace: str | None = None
    render_js: bool = False   # nyalakan untuk halaman yang butuh JavaScript


@router.post("/clip")
async def clip_url(
    request: ClipRequest,
    _: AuthDep,
    db: DbDep,
    embedding_svc: EmbedDep,
):
    """
    Simpan satu halaman web ke knowledge base.

    Halaman diambil, diubah jadi teks, lalu masuk pipeline yang sama dengan
    unggahan berkas — jadi hasilnya bisa dicari lewat rag_search seperti
    dokumen lain.
    """
    service = ClipperService(db=db, embedding_service=embedding_svc)
    return await service.clip(
        url=request.url,
        namespace=request.namespace,
        render_js=request.render_js,
    )


class ObsidianSyncRequest(BaseModel):
    # Menghapus tidak bisa dibatalkan, jadi harus diminta secara sadar
    prune: bool = False


@router.post("/obsidian/sync")
async def sync_obsidian(
    request: ObsidianSyncRequest,
    _: AuthDep,
    db: DbDep,
    embedding_svc: EmbedDep,
):
    """
    Baca vault Obsidian dari disk ke knowledge base.

    Hanya catatan yang berubah sejak sinkron terakhir yang diproses ulang —
    embedding mahal, dan vault bisa berisi ratusan catatan.
    """
    sync = ObsidianSync(db=db, embedding_service=embedding_svc)
    return await sync.run(prune=request.prune)


@router.get("/obsidian/status")
async def obsidian_status(_: AuthDep):
    """Apakah vault sudah diatur dan terbaca? Dipakai UI untuk memutuskan
    apakah tombol sinkron perlu ditampilkan."""
    p = vault_path()
    return {"configured": p is not None, "vault": str(p) if p else None}


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    _: AuthDep,
    db: DbDep,
    namespace: str | None = None,
    limit: int = 100,
):
    """List semua dokumen di knowledge base."""
    query = select(Document).order_by(Document.created_at.desc()).limit(limit)
    if namespace:
        query = query.where(Document.namespace == namespace)

    result = await db.execute(query)
    docs = result.scalars().all()

    return [
        DocumentOut(
            id=d.id,
            title=d.title,
            source_type=d.source_type,
            namespace=d.namespace,
            status=d.status,
            chunk_count=d.chunk_count,
            error_message=d.error_message,
            created_at=d.created_at.isoformat(),
        )
        for d in docs
    ]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    _: AuthDep,
    db: DbDep,
):
    """Hapus dokumen + semua chunks-nya (cascade)."""
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Dokumen tidak ditemukan.")

    await db.execute(
        sql_text("DELETE FROM documents WHERE id = :id"),
        {"id": document_id},
    )
    await db.commit()


@router.get("/stats")
async def knowledge_base_stats(
    _: AuthDep,
    db: DbDep,
):
    """Statistik knowledge base — total dokumen, chunks, per status."""
    doc_count = await db.scalar(select(func.count(Document.id)))
    chunk_count = await db.scalar(select(func.count(DocumentChunk.id)))

    status_result = await db.execute(
        select(Document.status, func.count(Document.id))
        .group_by(Document.status)
    )
    by_status = {row[0]: row[1] for row in status_result.all()}

    return {
        "total_documents": doc_count or 0,
        "total_chunks": chunk_count or 0,
        "by_status": by_status,
    }