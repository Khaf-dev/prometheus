"""
Memory Read Tool — Cari long-term memories yang relevan.

Agent memanggil tool ini saat butuh konteks dari percakapan sebelumnya.
Contoh: "gua pernah solve masalah ini sebelumnya?" → agent cari di memory.
"""

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import BaseTool, PermissionLevel, ToolParameter, ToolResult, ToolSchema, ToolStatus

logger = logging.getLogger(__name__)


class MemoryReadTool(BaseTool):
    """
    Semantic search ke long-term memory Prometheus.
    Pakai embedding + cosine similarity (pgvector HNSW index).
    """

    def __init__(self, db: AsyncSession, embedding_fn):
        """
        Args:
            db: async database session
            embedding_fn: async function(text) -> list[float]
                          dipanggil untuk embed query sebelum search
        """
        self._db = db
        self._embed = embedding_fn

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="memory_read",
            description=(
                "Cari long-term memories yang relevan dengan query. "
                "Gunakan ini untuk mengingat konteks dari percakapan sebelumnya, "
                "fakta tentang user, atau kejadian yang pernah didiskusikan."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="Apa yang ingin dicari di memory. Bisa berupa pertanyaan atau topik.",
                ),
                ToolParameter(
                    name="limit",
                    type="integer",
                    description="Jumlah maksimum memories yang dikembalikan (default: 5, max: 10).",
                    required=False,
                    default=5,
                ),
                ToolParameter(
                    name="memory_type",
                    type="string",
                    description="Filter by tipe memory: 'episodic' (kejadian) atau 'semantic' (fakta). Kosongkan untuk semua.",
                    required=False,
                    enum=["episodic", "semantic"],
                ),
                ToolParameter(
                    name="include_local_only",
                    type="boolean",
                    description="Sertakan memories yang ditandai local_only (default: true).",
                    required=False,
                    default=True,
                ),
            ],
            permission=PermissionLevel.READ_ONLY,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        query: str = kwargs["query"]
        # LLM kadang meminta limit=1 untuk pertanyaan yang justru butuh
        # beberapa fakta ("siapa saya?" perlu nama DAN pekerjaan). Jaga
        # batas bawahnya supaya jawaban tidak jadi timpang.
        limit: int = max(3, min(kwargs.get("limit", 5), 10))
        memory_type: str | None = kwargs.get("memory_type")
        include_local_only: bool = kwargs.get("include_local_only", True)

        try:
            # Embed query
            query_embedding = await self._embed(query)

            # Build SQL filter
            filters = ["embedding IS NOT NULL"]
            params: dict[str, Any] = {
                "embedding": str(query_embedding),
                "limit": limit,
                # 0.65 terlalu ketat untuk pertanyaan pendek. "siapakah gua"
                # dan "Nama user adalah Kaff" berhubungan secara makna tapi
                # nyaris tak berbagi kata — kemiripannya jatuh di sekitar
                # 0.4–0.6, jadi tersaring habis. Ambang longgar tetap aman
                # karena hasil sudah diurutkan dan dibatasi limit.
                "min_similarity": 0.35,
            }

            if not include_local_only:
                filters.append("is_local_only = false")
            if memory_type:
                filters.append("type = :memory_type")
                params["memory_type"] = memory_type

            where = " AND ".join(filters)

            rows = await self._db.execute(
                text(f"""
                    SELECT
                        id, type, content, is_local_only,
                        1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                    FROM memories
                    WHERE {where}
                        AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :min_similarity
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :limit
                """),
                params,
            )
            memories = rows.fetchall()

            if not memories:
                # Jangan berhenti di "tidak ada". Kalau memori memang tersimpan,
                # tunjukkan yang terbaru — lebih baik model punya bahan nyata
                # daripada menyimpulkan dirinya tidak mengenal penggunanya.
                recent = await self._db.execute(
                    text("""
                        SELECT id, type, content, is_local_only
                        FROM memories
                        WHERE embedding IS NOT NULL
                        ORDER BY created_at DESC
                        LIMIT :limit
                    """),
                    {"limit": limit},
                )
                rows_recent = recent.fetchall()
                if not rows_recent:
                    return ToolResult(
                        status=ToolStatus.SUCCESS,
                        output="Belum ada memory tersimpan sama sekali.",
                    )
                # Dahulukan DATA, bukan peringatan.
                #
                # Versi sebelumnya membuka dengan "Tidak ada yang cocok..." dan
                # model membaca itu sebagai kegagalan lalu berhenti — padahal
                # memorinya ada tepat di bawahnya. Cara sebuah hasil dibingkai
                # menentukan apakah model memakainya.
                return ToolResult(
                    status=ToolStatus.SUCCESS,
                    output={
                        "tentang": "KAFF, lawan bicaramu (bukan tentang dirimu)",
                        "memories": [
                            {"type": r.type, "content": r.content}
                            for r in rows_recent
                        ],
                        "catatan": "Diambil dari memory terbaru yang tersimpan.",
                    },
                    metadata={"count": len(rows_recent), "fallback": True},
                )

            results = []
            for m in memories:
                results.append({
                    "id": m.id,
                    "type": m.type,
                    "content": m.content,
                    "similarity": round(float(m.similarity), 3),
                    "local_only": m.is_local_only,
                })

            # Sebutkan pemilik fakta secara eksplisit. Daftar mentah membuat
            # model menebak subjeknya, dan tebakannya sering keliru — ia
            # mengira "Nama user adalah Kaff" adalah deskripsi dirinya.
            return ToolResult(
                status=ToolStatus.SUCCESS,
                output={
                    "tentang": "KAFF, lawan bicaramu (bukan tentang dirimu)",
                    "fakta": results,
                },
                metadata={"count": len(results), "query": query},
            )

        except Exception as e:
            logger.exception(f"memory_read failed for query: {query!r}")
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=str(e),
            )