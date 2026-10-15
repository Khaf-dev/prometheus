"""
Ingestion Service — Pipeline lengkap: parse → chunk → embed → store.

Ini orchestrator yang nyatuin semua komponen knowledge base:
1. parse_document()  → extract text dari file
2. FixedSizeChunker  → pecah jadi chunks
3. EmbeddingService  → embed tiap chunk (reuse dari memory system)
4. store ke DB       → Document + DocumentChunk

Design: async, dengan status tracking (processing → ready/failed).
Kalau ada error di tengah, Document di-mark "failed" dengan error message,
bukan crash — biar user tau dokumen mana yang gagal dan kenapa.
"""

import logging
import uuid

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.chunker import FixedSizeChunker
from app.knowledge.parsers import parse_document, ParserError
from app.memory.embeddings import EmbeddingService

logger = logging.getLogger(__name__)


class IngestionService:
    """
    Service untuk ingest dokumen ke knowledge base.

    Usage:
        service = IngestionService(db, embedding_service)
        doc_id = await service.ingest(
            content=file_bytes,
            filename="report.pdf",
            namespace="project:prometheus",
        )
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

    async def ingest(
        self,
        content: bytes,
        filename: str,
        namespace: str | None = None,
        source_uri: str | None = None,
    ) -> dict:
        """
        Ingest satu dokumen — full pipeline.

        Returns:
            {
                "document_id": str,
                "status": "ready" | "failed",
                "chunk_count": int,
                "error": str | None,
            }
        """
        # Step 0: Buat Document record dengan status "processing"
        doc_id = str(uuid.uuid4())
        await self._create_document(
            doc_id=doc_id,
            title=filename,
            source_uri=source_uri or filename,
            namespace=namespace,
        )

        try:
            # Step 1: Parse
            parse_result = parse_document(content, filename)
            source_type = parse_result.metadata.get("source_type", "text")

            # Update source_type di document
            await self._db.execute(
                sql_text("UPDATE documents SET source_type = :st WHERE id = :id"),
                {"st": source_type, "id": doc_id},
            )

            # Step 2: Chunk
            # Kalau ada info halaman (PDF), chunk per-halaman biar metadata page akurat
            all_chunks = []
            if parse_result.pages:
                for page in parse_result.pages:
                    page_chunks = self._chunker.chunk(
                        page.text,
                        base_metadata={
                            **parse_result.metadata,
                            "page": page.page_number,
                        },
                    )
                    all_chunks.extend(page_chunks)
            else:
                all_chunks = self._chunker.chunk(
                    parse_result.text,
                    base_metadata=parse_result.metadata,
                )

            if not all_chunks:
                raise ParserError("Dokumen tidak menghasilkan chunk (mungkin kosong).")

            # Step 3: Embed semua chunks (batch — efisien)
            chunk_texts = [c.content for c in all_chunks]
            embeddings = await self._embed_svc.embed_many(chunk_texts)

            # Step 4: Store chunks ke DB
            await self._store_chunks(doc_id, all_chunks, embeddings)

            # Step 5: Mark ready
            await self._db.execute(
                sql_text("""
                    UPDATE documents
                    SET status = 'ready', chunk_count = :cnt, updated_at = NOW()
                    WHERE id = :id
                """),
                {"cnt": len(all_chunks), "id": doc_id},
            )
            await self._db.commit()

            logger.info(f"Ingested '{filename}': {len(all_chunks)} chunks (doc={doc_id})")
            return {
                "document_id": doc_id,
                "status": "ready",
                "chunk_count": len(all_chunks),
                "error": None,
            }

        except (ParserError, Exception) as e:
            error_msg = str(e)
            logger.warning(f"Ingestion failed for '{filename}': {error_msg}")

            # Mark failed
            await self._db.execute(
                sql_text("""
                    UPDATE documents
                    SET status = 'failed', error_message = :err, updated_at = NOW()
                    WHERE id = :id
                """),
                {"err": error_msg[:500], "id": doc_id},
            )
            await self._db.commit()

            return {
                "document_id": doc_id,
                "status": "failed",
                "chunk_count": 0,
                "error": error_msg,
            }

    # ── DB helpers ────────────────────────────────────────────────────────────

    async def _create_document(
        self,
        doc_id: str,
        title: str,
        source_uri: str,
        namespace: str | None,
    ) -> None:
        await self._db.execute(
            sql_text("""
                INSERT INTO documents
                    (id, title, source_type, source_uri, namespace, status,
                     chunk_count, created_at, updated_at)
                VALUES
                    (:id, :title, 'processing', :uri, :ns, 'processing',
                     0, NOW(), NOW())
            """),
            {"id": doc_id, "title": title, "uri": source_uri, "ns": namespace},
        )
        await self._db.commit()

    async def _store_chunks(
        self,
        doc_id: str,
        chunks: list,
        embeddings: list[list[float]],
    ) -> None:
        """Bulk insert chunks dengan embeddings."""
        import json

        for chunk, embedding in zip(chunks, embeddings):
            await self._db.execute(
                sql_text("""
                    INSERT INTO document_chunks
                        (id, document_id, content, embedding, chunk_index,
                         metadata, created_at)
                    VALUES
                        (gen_random_uuid(), :doc_id, :content,
                         CAST(:embedding AS vector), :idx,
                         CAST(:meta AS jsonb), NOW())
                """),
                {
                    "doc_id": doc_id,
                    "content": chunk.content,
                    "embedding": str(embedding),
                    "idx": chunk.index,
                    "meta": json.dumps(chunk.metadata),
                },
            )