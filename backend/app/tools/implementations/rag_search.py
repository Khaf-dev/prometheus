"""
RAG Retrieval Tool — Agent bisa search knowledge base dokumen.

Reuse pola hybrid search dari memory system (Phase 2): vector similarity
digabung dengan keyword search. Tapi target-nya tabel document_chunks,
bukan memories.

Agent manggil tool ini pas butuh info dari dokumen yang di-upload user.
Contoh: "apa isi kontrak yang gua upload?" atau "cari bagian tentang
authentication di dokumen API".
"""

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import (
    BaseTool,
    PermissionLevel,
    ToolParameter,
    ToolResult,
    ToolSchema,
    ToolStatus,
)

logger = logging.getLogger(__name__)

# RRF constant — sama dengan memory system biar konsisten
RRF_K = 60
CANDIDATE_POOL = 20


def _sanitize_tsquery_token(token: str) -> str:
    """Bersihkan token untuk to_tsquery (sama seperti memory manager)."""
    import re
    cleaned = re.sub(r"[^\w]", "", token, flags=re.UNICODE)
    return cleaned.lower().strip()


class RAGSearchTool(BaseTool):
    """Search knowledge base dokumen dengan hybrid search."""

    def __init__(self, db: AsyncSession, embedding_fn):
        self._db = db
        self._embed = embedding_fn

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="rag_search",
            description=(
                "Cari di dokumen yang SUDAH TERSIMPAN di knowledge base user "
                "(PDF, markdown, kode, artikel web yang pernah di-clip). "
                "PAKAI INI DULU setiap kali user bertanya tentang 'dokumen gua', "
                "'artikel yang gua simpan', 'catatan gua', atau isi apa pun yang "
                "sudah pernah dia simpan. JANGAN pakai browser untuk ini — "
                "browser hanya untuk membuka URL baru yang user berikan sekarang."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Pertanyaan atau kata kunci yang dicari di dokumen.",
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Jumlah chunk maksimum yang dikembalikan (default: 5).",
                    required=False,
                    default=5,
                ),
                ToolParameter(
                    name="namespace",
                    type="string",
                    description=(
                        "Filter ke namespace tertentu (misal 'project:prometheus'). "
                        "Kosongkan untuk cari di semua dokumen."
                    ),
                    required=False,
                    default="",
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        query: str = kwargs["query"]
        # LLM sering ngirim angka sebagai string ("5" bukan 5), jadi coerce dulu
        # sebelum operasi numerik. Kalau gagal convert, pakai default.
        try:
            limit = min(int(kwargs.get("limit", 5)), 15)
        except (ValueError, TypeError):
            limit = 5
        namespace: str = str(kwargs.get("namespace", "") or "")

        if not query.strip():
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error="Query tidak boleh kosong.",
            )

        try:
            query_embedding = await self._embed(query)

            # OR-based tsquery (sama seperti memory hybrid search)
            tokens = [
                _sanitize_tsquery_token(t)
                for t in query.split()
                if _sanitize_tsquery_token(t)
            ]
            or_tsquery = " | ".join(tokens) if tokens else ""

            ns_filter = ""
            params: dict = {
                "embedding": str(query_embedding),
                "or_tsquery": or_tsquery,
                "limit": limit,
                "rrf_k": RRF_K,
                "candidate_pool": CANDIDATE_POOL,
            }
            if namespace:
                ns_filter = "AND d.namespace = :namespace"
                params["namespace"] = namespace

            # Hybrid search di document_chunks, join ke documents untuk metadata
            result = await self._db.execute(
                text(f"""
                    WITH vector_search AS (
                        SELECT
                            dc.id,
                            ROW_NUMBER() OVER (
                                ORDER BY dc.embedding <=> CAST(:embedding AS vector)
                            ) AS vec_rank
                        FROM document_chunks dc
                        JOIN documents d ON d.id = dc.document_id
                        WHERE dc.embedding IS NOT NULL
                            AND d.status = 'ready'
                            {ns_filter}
                        ORDER BY dc.embedding <=> CAST(:embedding AS vector)
                        LIMIT :candidate_pool
                    ),
                    keyword_search AS (
                        SELECT
                            dc.id,
                            ROW_NUMBER() OVER (
                                ORDER BY ts_rank(
                                    to_tsvector('simple', dc.content),
                                    to_tsquery('simple', :or_tsquery)
                                ) DESC
                            ) AS kw_rank
                        FROM document_chunks dc
                        JOIN documents d ON d.id = dc.document_id
                        WHERE :or_tsquery <> ''
                            AND d.status = 'ready'
                            AND to_tsvector('simple', dc.content) @@ to_tsquery('simple', :or_tsquery)
                            {ns_filter}
                        ORDER BY ts_rank(
                            to_tsvector('simple', dc.content),
                            to_tsquery('simple', :or_tsquery)
                        ) DESC
                        LIMIT :candidate_pool
                    ),
                    fused AS (
                        SELECT
                            COALESCE(v.id, k.id) AS id,
                            COALESCE(1.0 / (:rrf_k + v.vec_rank), 0.0) +
                            COALESCE(1.0 / (:rrf_k + k.kw_rank), 0.0) AS rrf_score
                        FROM vector_search v
                        FULL OUTER JOIN keyword_search k ON v.id = k.id
                    )
                    SELECT
                        dc.content,
                        dc.chunk_index,
                        dc.metadata,
                        d.title AS doc_title,
                        d.source_type,
                        f.rrf_score
                    FROM fused f
                    JOIN document_chunks dc ON dc.id = f.id
                    JOIN documents d ON d.id = dc.document_id
                    ORDER BY f.rrf_score DESC
                    LIMIT :limit
                """),
                params,
            )

            rows = result.fetchall()

            if not rows:
                # Pencarian kosong sering terjadi ketika pengguna bertanya
                # TENTANG dokumen ("ringkas artikelnya", "apa kesimpulannya")
                # alih-alih tentang ISI-nya. Kata seperti "ringkas" atau
                # "kesimpulan" tidak ada di dalam teks dokumen mana pun.
                #
                # Menjawab "tidak ketemu" pada kasus itu keliru dan berbahaya:
                # model bisa mengarang seolah sudah membaca. Jadi kita kirim
                # isi awal dokumen terbaru — bahan nyata untuk diringkas.
                fb = await self._db.execute(
                    text(f"""
                        SELECT dc.content, dc.chunk_index, d.title, d.source_type
                        FROM document_chunks dc
                        JOIN documents d ON d.id = dc.document_id
                        WHERE d.status = 'ready' {ns_filter}
                          AND d.id = (
                              SELECT id FROM documents
                              WHERE status = 'ready' {ns_filter}
                              ORDER BY created_at DESC LIMIT 1
                          )
                        ORDER BY dc.chunk_index
                        LIMIT :limit
                    """),
                    {k: v for k, v in params.items() if k in ("limit", "namespace")},
                )
                fb_rows = fb.fetchall()
                if not fb_rows:
                    return ToolResult(
                        status=ToolStatus.SUCCESS,
                        output="Knowledge base masih kosong — belum ada dokumen tersimpan.",
                        metadata={"count": 0},
                    )
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    output={
                        "sumber": fb_rows[0].title,
                        "catatan": "Diambil dari bagian awal dokumen terbaru.",
                        "potongan": [
                            {"content": r.content, "chunk_index": r.chunk_index}
                            for r in fb_rows
                        ],
                    },
                    metadata={"count": len(fb_rows), "fallback": True},
                )

            results = []
            for row in rows:
                meta = row.metadata or {}
                results.append({
                    "content": row.content,
                    "source": row.doc_title,
                    "source_type": row.source_type,
                    "page": meta.get("page"),
                    "chunk_index": row.chunk_index,
                    "relevance_score": round(float(row.rrf_score), 4),
                })

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output=results,
                metadata={"count": len(results), "query": query},
            )

        except Exception as e:
            logger.exception("rag_search failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))


class KnowledgeListTool(BaseTool):
    """Sebutkan dokumen apa saja yang ada di knowledge base."""

    def __init__(self, db: AsyncSession):
        self._db = db

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="knowledge_list",
            description=(
                "Sebutkan DAFTAR dokumen yang tersimpan di knowledge base. "
                "Pakai ini kalau user bertanya 'dokumen apa aja yang gua punya', "
                "'ada artikel apa yang gua simpan', atau ingin tahu ISI perpustakaan. "
                "Untuk mencari sesuatu DI DALAM dokumen, pakai rag_search."
            ),
            parameters=[
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Jumlah dokumen maksimum (default: 20).",
                    required=False,
                    default=20,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        try:
            limit = min(int(kwargs.get("limit", 20)), 50)
        except (ValueError, TypeError):
            limit = 20

        try:
            result = await self._db.execute(
                text("""
                    SELECT title, source_type, source_uri, chunk_count, status, created_at
                    FROM documents ORDER BY created_at DESC LIMIT :limit
                """),
                {"limit": limit},
            )
            rows = result.fetchall()

            if not rows:
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    output="Knowledge base masih kosong. Belum ada dokumen yang diunggah.",
                    metadata={"count": 0},
                )

            docs = [
                {
                    "judul": r.title,
                    "tipe": r.source_type,
                    "sumber": r.source_uri,
                    "potongan": r.chunk_count,
                    "status": r.status,
                    "disimpan": r.created_at.strftime("%d %b %Y"),
                }
                for r in rows
            ]
            return ToolResult(
                status=ToolStatus.SUCCESS, output=docs, metadata={"count": len(docs)}
            )
        except Exception as e:
            logger.exception("knowledge_list failed")
            return ToolResult(status=ToolStatus.ERROR, output=None, error=str(e))