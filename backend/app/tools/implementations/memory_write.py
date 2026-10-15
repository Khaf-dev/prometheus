"""
Memory Write Tool — Simpan memory baru ke long-term storage.

Agent memanggil ini untuk menyimpan informasi penting dari conversation
yang mungkin berguna di masa depan.

Contoh:
- "Kaff prefer pakai async/await bukan callbacks"
- "Deploy production Prometheus dilakukan tanggal 14 Juni 2026"
- "Bug authentication solved dengan set 127.0.0.1 instead of localhost"
"""

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import BaseTool, PermissionLevel, ToolParameter, ToolResult, ToolSchema, ToolStatus

logger = logging.getLogger(__name__)


class MemoryWriteTool(BaseTool):
    """
    Simpan memory baru ke pgvector dengan auto-embedding.
    """

    def __init__(self, db: AsyncSession, embedding_fn, conversation_id: str | None = None):
        """
        Args:
            db: async database session
            embedding_fn: async function(text) -> list[float]
            conversation_id: source conversation UUID untuk audit trail
        """
        self._db = db
        self._embed = embedding_fn
        self._conversation_id = conversation_id

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="memory_write",
            description=(
                "Simpan informasi penting ke long-term memory. "
                "Gunakan ini untuk menyimpan fakta tentang user, preferensi, "
                "keputusan teknis, atau kejadian penting yang mungkin berguna "
                "di percakapan mendatang."
            ),
            parameters=[
                ToolParameter(
                    name="content",
                    type="string",
                    description=(
                        "Informasi yang ingin disimpan. Tulis dengan jelas dan lengkap "
                        "karena ini akan dicari secara semantic di masa depan. "
                        "Contoh: 'Kaff menggunakan Windows dengan Docker Desktop untuk development Prometheus'"
                    ),
                ),
                ToolParameter(
                    name="memory_type",
                    type="string",
                    description=(
                        "Tipe memory: "
                        "'episodic' untuk kejadian/event spesifik yang terikat waktu, "
                        "'semantic' untuk fakta stabil tentang user atau preferensi."
                    ),
                    enum=["episodic", "semantic"],
                ),
                ToolParameter(
                    name="is_local_only",
                    type="boolean",
                    description=(
                        "Tandai sebagai local-only jika memory mengandung informasi sensitif "
                        "yang tidak boleh dikirim ke cloud LLM sebagai context. "
                        "Default: false."
                    ),
                    required=False,
                    default=False,
                ),
            ],
            permission=PermissionLevel.READ_WRITE,
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        content: str = kwargs["content"]
        memory_type: str = kwargs["memory_type"]
        is_local_only: bool = kwargs.get("is_local_only", False)

        if not content.strip():
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error="Content tidak boleh kosong.",
            )

        if memory_type not in ("episodic", "semantic"):
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=f"memory_type harus 'episodic' atau 'semantic', bukan '{memory_type}'.",
            )

        try:
            # Generate embedding
            embedding = await self._embed(content)

            # Insert ke DB
            result = await self._db.execute(
                text("""
                    INSERT INTO memories
                        (id, type, content, embedding, source_conversation_id,
                         is_local_only, created_at, updated_at)
                    VALUES (
                        gen_random_uuid(),
                        :memory_type,
                        :content,
                        CAST(:embedding AS vector),
                        :conversation_id,
                        :is_local_only,
                        NOW(), NOW()
                    )
                    RETURNING id
                """),
                {
                    "memory_type": memory_type,
                    "content": content,
                    "embedding": str(embedding),
                    "conversation_id": self._conversation_id,
                    "is_local_only": is_local_only,
                },
            )
            row = result.fetchone()
            memory_id = str(row.id) if row else "unknown"

            return ToolResult(
                status=ToolStatus.SUCCESS,
                output={
                    "memory_id": memory_id,
                    "type": memory_type,
                    "content": content,
                    "local_only": is_local_only,
                    "message": "Memory berhasil disimpan.",
                },
                metadata={"memory_id": memory_id},
            )

        except Exception as e:
            logger.exception(f"memory_write failed for content: {content[:50]!r}")
            return ToolResult(
                status=ToolStatus.ERROR,
                output=None,
                error=str(e),
            )