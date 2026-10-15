"""
Vector search utilities untuk Prometheus.

Dua use case utama:
1. Memory retrieval — cari memories yang relevan dengan query
2. RAG retrieval — cari document chunks yang relevan

Keduanya pakai cosine similarity (vector_cosine_ops di HNSW index).
Cosine dipilih karena: invariant terhadap magnitude,
cocok untuk semantic similarity di embedding space.
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class MemorySearchResult:
    id: str
    type: str
    content: str
    similarity: float
    is_local_only: bool


@dataclass
class ChunkSearchResult:
    id: str
    document_id: str
    content: str
    chunk_index: int
    similarity: float
    metadata: dict
    document_title: str
    document_namespace: str | None


async def search_memories(
    db: AsyncSession,
    query_embedding: list[float],
    limit: int = 5,
    min_similarity: float = 0.7,
    include_local_only: bool = True,
    memory_type: str | None = None,
) -> list[MemorySearchResult]:
    """
    Cari memories yang relevan dengan query embedding.

    Args:
        query_embedding: embedding dari query user (1536-dim)
        limit: max hasil yang dikembalikan
        min_similarity: threshold cosine similarity (0-1), 0.7 = cukup relevan
        include_local_only: kalau False, skip memory yang is_local_only=True
                           (dipakai saat mau inject ke cloud LLM context)
        memory_type: filter by 'episodic' atau 'semantic', None = semua

    Returns:
        List memories sorted by similarity descending
    """
    filters = ["embedding IS NOT NULL"]

    if not include_local_only:
        filters.append("is_local_only = false")

    if memory_type:
        filters.append(f"type = :memory_type")

    where_clause = " AND ".join(filters)

    # Operator <=> = cosine distance (bukan similarity)
    # similarity = 1 - distance
    query = text(f"""
        SELECT
            id,
            type,
            content,
            is_local_only,
            1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM memories
        WHERE {where_clause}
            AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :min_similarity
        ORDER BY embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """)

    params: dict = {
        "embedding": str(query_embedding),
        "min_similarity": min_similarity,
        "limit": limit,
    }
    if memory_type:
        params["memory_type"] = memory_type

    result = await db.execute(query, params)
    rows = result.fetchall()

    return [
        MemorySearchResult(
            id=row.id,
            type=row.type,
            content=row.content,
            is_local_only=row.is_local_only,
            similarity=float(row.similarity),
        )
        for row in rows
    ]


async def search_document_chunks(
    db: AsyncSession,
    query_embedding: list[float],
    limit: int = 5,
    min_similarity: float = 0.6,
    namespace: str | None = None,
    document_ids: list[str] | None = None,
) -> list[ChunkSearchResult]:
    """
    RAG retrieval — cari document chunks yang relevan.

    Args:
        query_embedding: embedding dari query user
        limit: max chunks yang dikembalikan
        min_similarity: threshold similarity (0.6 lebih rendah dari memory karena
                        dokumen teknis bisa punya wording berbeda tapi relevan)
        namespace: filter by namespace ("project:prometheus", "topic:odoo")
        document_ids: filter by specific document IDs

    Returns:
        List chunks sorted by similarity descending, dengan document context
    """
    filters = ["dc.embedding IS NOT NULL", "d.status = 'ready'"]

    if namespace:
        filters.append("d.namespace = :namespace")

    if document_ids:
        filters.append("dc.document_id = ANY(:document_ids)")

    where_clause = " AND ".join(filters)

    query = text(f"""
        SELECT
            dc.id,
            dc.document_id,
            dc.content,
            dc.chunk_index,
            dc.metadata,
            d.title AS document_title,
            d.namespace AS document_namespace,
            1 - (dc.embedding <=> CAST(:embedding AS vector)) AS similarity
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE {where_clause}
            AND 1 - (dc.embedding <=> CAST(:embedding AS vector)) >= :min_similarity
        ORDER BY dc.embedding <=> CAST(:embedding AS vector)
        LIMIT :limit
    """)

    params: dict = {
        "embedding": str(query_embedding),
        "min_similarity": min_similarity,
        "limit": limit,
    }
    if namespace:
        params["namespace"] = namespace
    if document_ids:
        params["document_ids"] = document_ids

    result = await db.execute(query, params)
    rows = result.fetchall()

    return [
        ChunkSearchResult(
            id=row.id,
            document_id=row.document_id,
            content=row.content,
            chunk_index=row.chunk_index,
            similarity=float(row.similarity),
            metadata=row.metadata or {},
            document_title=row.document_title,
            document_namespace=row.document_namespace,
        )
        for row in rows
    ]


async def hybrid_search_chunks(
    db: AsyncSession,
    query_embedding: list[float],
    query_text: str,
    limit: int = 5,
    namespace: str | None = None,
    vector_weight: float = 0.7,
    text_weight: float = 0.3,
) -> list[ChunkSearchResult]:
    """
    Hybrid search: kombinasi vector similarity + BM25-style text search.
    Sesuai dengan FR-013: "hybrid search (vector similarity + BM25 keyword)".

    Kenapa hybrid?
    - Vector: bagus untuk semantic similarity ("cara auth" ↔ "proses autentikasi")
    - Text: bagus untuk exact match (nama fungsi, error code, API endpoint spesifik)
    - Kombinasi = coverage terbaik

    vector_weight + text_weight harus = 1.0
    """
    assert abs(vector_weight + text_weight - 1.0) < 0.01, "Weights harus total 1.0"

    namespace_filter = "AND d.namespace = :namespace" if namespace else ""

    # RRF (Reciprocal Rank Fusion) — cara standard untuk gabung dua ranking
    # Score = vector_weight * (1/vector_rank) + text_weight * (1/text_rank)
    query = text(f"""
        WITH vector_results AS (
            SELECT
                dc.id,
                ROW_NUMBER() OVER (ORDER BY dc.embedding <=> CAST(:embedding AS vector)) AS rank,
                1 - (dc.embedding <=> CAST(:embedding AS vector)) AS vector_score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.embedding IS NOT NULL
                AND d.status = 'ready'
                {namespace_filter}
            ORDER BY dc.embedding <=> CAST(:embedding AS vector)
            LIMIT 20
        ),
        text_results AS (
            SELECT
                dc.id,
                ROW_NUMBER() OVER (ORDER BY similarity(dc.content, :query_text) DESC) AS rank,
                similarity(dc.content, :query_text) AS text_score
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.content % :query_text   -- pg_trgm similarity operator
                AND d.status = 'ready'
                {namespace_filter}
            ORDER BY similarity(dc.content, :query_text) DESC
            LIMIT 20
        ),
        combined AS (
            SELECT
                COALESCE(v.id, t.id) AS id,
                COALESCE(v.vector_score, 0) AS vector_score,
                COALESCE(t.text_score, 0) AS text_score,
                (:vector_weight * COALESCE(1.0 / NULLIF(v.rank, 0), 0) +
                 :text_weight  * COALESCE(1.0 / NULLIF(t.rank, 0), 0)) AS rrf_score
            FROM vector_results v
            FULL OUTER JOIN text_results t ON v.id = t.id
        )
        SELECT
            dc.id,
            dc.document_id,
            dc.content,
            dc.chunk_index,
            dc.metadata,
            d.title AS document_title,
            d.namespace AS document_namespace,
            c.rrf_score AS similarity
        FROM combined c
        JOIN document_chunks dc ON dc.id = c.id
        JOIN documents d ON d.id = dc.document_id
        ORDER BY c.rrf_score DESC
        LIMIT :limit
    """)

    params: dict = {
        "embedding": str(query_embedding),
        "query_text": query_text,
        "vector_weight": vector_weight,
        "text_weight": text_weight,
        "limit": limit,
    }
    if namespace:
        params["namespace"] = namespace

    result = await db.execute(query, params)
    rows = result.fetchall()

    return [
        ChunkSearchResult(
            id=row.id,
            document_id=row.document_id,
            content=row.content,
            chunk_index=row.chunk_index,
            similarity=float(row.similarity),
            metadata=row.metadata or {},
            document_title=row.document_title,
            document_namespace=row.document_namespace,
        )
        for row in rows
    ]