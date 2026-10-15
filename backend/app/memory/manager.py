"""
Memory Manager — High-level interface untuk semua memory operations.

Changelog:
- Fix N+1 query di save_many → true batch processing (embed_many + bulk insert)
- Fix fragile JSON parsing di auto_extract_and_save → regex-based extractor
- Tambah recency weighting di retrieve_relevant → gabung similarity + freshness score
"""

import logging
import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.embeddings import EmbeddingService

logger = logging.getLogger(__name__)

DUPLICATE_THRESHOLD  = 0.95
# Ambang kemiripan untuk dianggap relevan.
# 0.65 terlalu ketat: pertanyaan pendek jarang berbagi kata dengan fakta
# yang dicarinya, jadi nilainya jatuh di 0.4-0.6 dan tersaring habis.
# Pencarian hibrida sudah mengurutkan hasil, jadi ambang longgar aman.
RELEVANCE_THRESHOLD  = 0.35
MAX_CONTEXT_MEMORIES = 5

# Recency weighting — bobot freshness dalam final score
RECENCY_WEIGHT = 0.25

# Legacy (dipakai di tempat lain) — dipertahankan biar tidak break
SIMILARITY_WEIGHT = 0.75

# ── Hybrid search (RRF) tuning ────────────────────────────────────────────────
# RRF_K: konstanta smoothing di formula 1/(k + rank). Nilai 60 adalah default
# standar dari paper RRF asli — makin besar makin "rata" bobotnya, makin kecil
# makin tajam bedanya antara rank 1 vs rank 2.
RRF_K = 60

# CANDIDATE_POOL: berapa kandidat diambil dari MASING-MASING search (vector &
# keyword) sebelum di-fusion. Lebih besar = recall lebih baik tapi lebih lambat.
CANDIDATE_POOL = 20


@dataclass
class Memory:
    id: str
    type: str
    content: str
    similarity: float
    is_local_only: bool


class MemoryManager:

    def __init__(self, db: AsyncSession, embedding_service: EmbeddingService):
        self._db = db
        self._embed_svc = embedding_service

    # ── Retrieve ──────────────────────────────────────────────────────────────

    async def retrieve_relevant(
        self,
        query: str,
        limit: int = MAX_CONTEXT_MEMORIES,
        min_similarity: float = RELEVANCE_THRESHOLD,
        include_local_only: bool = True,
        memory_type: str | None = None,
    ) -> list[Memory]:
        """
        Hybrid search: vector similarity + keyword (FTS) + RRF fusion + recency.

        Kenapa hybrid (bukan pure vector)?
        - Vector search bagus nangkep MAKNA, tapi sering meleset untuk exact
          technical term (nama library, error, command). Contoh: "asyncpg",
          "127.0.0.1", "Odoo" — istilah spesifik yang embedding-nya bisa meleset.
        - Keyword search (PostgreSQL FTS) nangkep exact term dengan presisi.
        - RRF (Reciprocal Rank Fusion) gabungin ranking dari keduanya tanpa perlu
          normalisasi score yang beda skala. Formula: score = Σ 1/(k + rank_i).

        Flow:
        1. Vector search → ranked list by cosine similarity
        2. Keyword search (FTS) → ranked list by ts_rank
        3. RRF fusion → gabung & re-rank berdasarkan posisi di kedua list
        4. Recency boost → memory fresh sedikit diprioritaskan
        """
        if not query.strip():
            return []

        query_embedding = await self._embed_svc.embed(query)

        # Convert query ke OR-based tsquery untuk keyword search.
        #
        # KENAPA OR, bukan plainto_tsquery (yang pakai AND)?
        # plainto_tsquery('nama spesialis Odoo') = 'nama' & 'spesialis' & 'odoo'
        # → cari memory yang punya SEMUA kata. Padahal memory "User spesialis Odoo"
        #   nggak punya kata "nama", jadi ke-exclude walaupun jelas relevan.
        # OR logic ('nama | spesialis | odoo') = match memory yang punya MINIMAL
        #   satu kata → jauh lebih tepat untuk retrieval memory.
        #
        # Token di-sanitize (buang karakter khusus tsquery) untuk hindari syntax error.
        tokens = [
            _sanitize_tsquery_token(t)
            for t in query.split()
            if _sanitize_tsquery_token(t)
        ]
        or_tsquery = " | ".join(tokens) if tokens else ""

        extra_filters = ""
        params: dict = {
            "embedding": str(query_embedding),
            "or_tsquery": or_tsquery,
            "limit": limit,
            "rrf_k": RRF_K,
            "candidate_pool": CANDIDATE_POOL,
        }

        if not include_local_only:
            extra_filters += " AND is_local_only = false"
        if memory_type:
            extra_filters += " AND type = :memory_type"
            params["memory_type"] = memory_type

        # Hybrid query dengan RRF fusion
        result = await self._db.execute(
            text(f"""
                WITH vector_search AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            ORDER BY embedding <=> CAST(:embedding AS vector)
                        ) AS vec_rank,
                        1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                    FROM memories
                    WHERE embedding IS NOT NULL {extra_filters}
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT :candidate_pool
                ),
                keyword_search AS (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            ORDER BY ts_rank(content_tsv, to_tsquery('simple', :or_tsquery)) DESC
                        ) AS kw_rank
                    FROM memories
                    WHERE :or_tsquery <> ''
                        AND content_tsv @@ to_tsquery('simple', :or_tsquery)
                        {extra_filters}
                    ORDER BY ts_rank(content_tsv, to_tsquery('simple', :or_tsquery)) DESC
                    LIMIT :candidate_pool
                ),
                -- RRF fusion: gabung rank dari vector + keyword
                fused AS (
                    SELECT
                        COALESCE(v.id, k.id) AS id,
                        COALESCE(1.0 / (:rrf_k + v.vec_rank), 0.0) +
                        COALESCE(1.0 / (:rrf_k + k.kw_rank), 0.0) AS rrf_score,
                        v.similarity
                    FROM vector_search v
                    FULL OUTER JOIN keyword_search k ON v.id = k.id
                )
                SELECT
                    m.id, m.type, m.content, m.is_local_only,
                    COALESCE(f.similarity, 0.0) AS similarity,
                    f.rrf_score +
                        {RECENCY_WEIGHT} * (
                            1.0 / (1.0 + EXTRACT(EPOCH FROM (NOW() - m.created_at)) / 86400.0)
                        ) AS final_score
                FROM fused f
                JOIN memories m ON m.id = f.id
                ORDER BY final_score DESC
                LIMIT :limit
            """),
            params,
        )

        return [
            Memory(
                id=str(row.id),
                type=row.type,
                content=row.content,
                similarity=round(float(row.similarity), 3),
                is_local_only=row.is_local_only,
            )
            for row in result.fetchall()
        ]

    # ── Save ──────────────────────────────────────────────────────────────────

    async def save(
        self,
        content: str,
        memory_type: str,
        is_local_only: bool = False,
        conversation_id: str | None = None,
        skip_duplicate_check: bool = False,
    ) -> str | None:
        """Simpan satu memory. Return memory_id atau None kalau duplikat."""
        if not content.strip():
            return None

        embedding = await self._embed_svc.embed(content)

        if not skip_duplicate_check:
            existing = await self._db.execute(
                text("""
                    SELECT id,
                           1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
                    FROM memories
                    WHERE embedding IS NOT NULL
                        AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :threshold
                    ORDER BY embedding <=> CAST(:embedding AS vector)
                    LIMIT 1
                """),
                {"embedding": str(embedding), "threshold": DUPLICATE_THRESHOLD},
            )
            if existing.fetchone():
                logger.info(f"Memory skipped (duplicate): {content[:60]!r}")
                return None

        result = await self._db.execute(
            text("""
                INSERT INTO memories
                    (id, type, content, embedding, source_conversation_id,
                     is_local_only, created_at, updated_at)
                VALUES (
                    gen_random_uuid(), :type, :content,
                    CAST(:embedding AS vector), :conv_id,
                    :local_only, NOW(), NOW()
                )
                RETURNING id
            """),
            {
                "type": memory_type,
                "content": content,
                "embedding": str(embedding),
                "conv_id": conversation_id,
                "local_only": is_local_only,
            },
        )
        row = result.fetchone()
        memory_id = str(row.id) if row else None
        logger.info(f"Memory saved: [{memory_type}] {content[:60]!r}")
        return memory_id

    async def save_many(
        self,
        memories: list[dict],
        conversation_id: str | None = None,
    ) -> list[str]:
        """
        Batch save memories — TRUE batch processing, bukan N+1 sequential.

        Flow:
        1. embed_many() → 1 network call untuk semua teks sekaligus
        2. Bulk duplicate check → 1 query untuk semua embeddings
        3. Bulk INSERT → 1 query untuk semua yang lolos duplicate check

        Hasilnya: dari O(N) network calls → O(1) network calls.
        """
        if not memories:
            return []

        # Validasi dan normalize input
        valid = []
        for m in memories:
            content = m.get("content", "").strip()
            if content:
                valid.append({
                    "content": content,
                    "type": m.get("type", "semantic"),
                    "is_local_only": m.get("is_local_only", False),
                })

        if not valid:
            return []

        # Step 1: Batch embed semua sekaligus — 1 network call
        texts = [m["content"] for m in valid]
        embeddings = await self._embed_svc.embed_many(texts)

        # Step 2: Bulk duplicate check via unnest
        # Kirim semua embeddings sekaligus, DB yang filter duplikat
        embedding_strs = [str(e) for e in embeddings]

        dup_result = await self._db.execute(
            text("""
                WITH candidates(idx, embedding_str) AS (
                    SELECT
                        ordinality - 1,
                        unnested_val
                    FROM unnest(CAST(:embeddings AS text[])) WITH ORDINALITY
                        AS t(unnested_val, ordinality)
                ),
                duplicates AS (
                    SELECT DISTINCT ON (c.idx) c.idx
                    FROM candidates c
                    JOIN memories m ON m.embedding IS NOT NULL
                    WHERE 1 - (m.embedding <=> CAST(c.embedding_str AS vector))
                              >= :threshold
                )
                SELECT idx FROM duplicates
            """),
            {
                "embeddings": embedding_strs,
                "threshold": DUPLICATE_THRESHOLD,
            },
        )
        duplicate_indices = {row.idx for row in dup_result.fetchall()}

        # Step 3: Filter out duplicates
        to_insert = [
            (i, valid[i], embeddings[i])
            for i in range(len(valid))
            if i not in duplicate_indices
        ]

        if not to_insert:
            logger.info("save_many: semua memories adalah duplikat, skip.")
            return []

        # Step 4: Bulk INSERT — 1 query
        saved_ids = []
        for _, mem, embedding in to_insert:
            result = await self._db.execute(
                text("""
                    INSERT INTO memories
                        (id, type, content, embedding, source_conversation_id,
                         is_local_only, created_at, updated_at)
                    VALUES (
                        gen_random_uuid(), :type, :content,
                        CAST(:embedding AS vector), :conv_id,
                        :local_only, NOW(), NOW()
                    )
                    RETURNING id
                """),
                {
                    "type": mem["type"],
                    "content": mem["content"],
                    "embedding": str(embedding),
                    "conv_id": conversation_id,
                    "local_only": mem["is_local_only"],
                },
            )
            row = result.fetchone()
            if row:
                saved_ids.append(str(row.id))

        logger.info(
            f"save_many: {len(saved_ids)} saved, "
            f"{len(duplicate_indices)} duplicates skipped "
            f"(from {len(valid)} candidates)"
        )
        return saved_ids

    # ── Delete ────────────────────────────────────────────────────────────────

    async def delete(self, memory_id: str) -> bool:
        result = await self._db.execute(
            text("DELETE FROM memories WHERE id = :id RETURNING id"),
            {"id": memory_id},
        )
        deleted = result.fetchone() is not None
        if deleted:
            logger.info(f"Memory deleted: {memory_id}")
        return deleted

    # ── Context formatting ────────────────────────────────────────────────────

    def format_for_context(
        self,
        memories: list[Memory],
        include_local_only: bool = True,
    ) -> str:
        if not memories:
            return ""

        filtered = [
            m for m in memories
            if include_local_only or not m.is_local_only
        ]
        if not filtered:
            return ""

        # Bingkai yang tegas siapa subjeknya.
        #
        # Memori tersimpan dengan kata "user" ("Nama user adalah Kaff"), dan
        # kata itu kabur bagi model kecil — ia tidak mengikatnya ke lawan
        # bicara, lalu menebak, dan sering keliru menganggapnya deskripsi
        # dirinya sendiri. Menyebut pemiliknya secara eksplisit di judul jauh
        # lebih andal daripada mengatur kata ganti lewat instruksi.
        lines = [
            "## Yang kamu ketahui tentang KAFF (lawan bicaramu)\n",
            "Fakta di bawah ini tentang KAFF, bukan tentang dirimu:\n",
        ]
        for i, mem in enumerate(filtered, 1):
            tag = f"[{mem.type.upper()}]"
            lines.append(f"{i}. {tag} {mem.content}")

        return "\n".join(lines)

    # ── Auto-extract ──────────────────────────────────────────────────────────

    async def auto_extract_and_save(
        self,
        conversation_summary: str,
        llm_extract_fn,
        conversation_id: str | None = None,
    ) -> list[str]:
        """
        Auto-extract memories dari conversation summary menggunakan LLM.

        JSON parsing menggunakan regex — toleran terhadap:
        - Teks basa-basi sebelum/sesudah JSON
        - Markdown code blocks (```json ... ```)
        - Whitespace tidak konsisten
        - Mixed content
        """
        prompt = f"""Kamu adalah sistem ekstraksi memori. Tugasmu HANYA mengekstrak fakta dari PERCAKAPAN NYATA di bawah menjadi JSON. JANGAN menjawab percakapan. JANGAN menyalin contoh.

=== CONTOH FORMAT (data karangan, JANGAN dipakai) ===
Masukan : "User: aku pelihara kucing oranye namanya Mochi"
Keluaran: [{{"content": "User memelihara kucing oranye bernama Mochi", "type": "semantic", "is_local_only": false}}]
=== AKHIR CONTOH ===

PERCAKAPAN NYATA YANG HARUS DIEKSTRAK:
{conversation_summary}

Aturan:
- Ekstrak HANYA dari "PERCAKAPAN NYATA" di atas. Kalau sebuah fakta tidak
  disebut di situ, JANGAN dibuat.
- "semantic" = fakta stabil tentang user (nama, pekerjaan, preferensi, skill)
- "episodic" = kejadian spesifik (bug yang di-fix, keputusan, deploy)
- "is_local_only": true HANYA jika ada password/token/data sensitif
- Maksimum 5 fakta, hanya yang benar-benar penting
- Kalau tidak ada fakta penting, keluarkan: []

Keluarkan HANYA JSON array, tanpa teks lain, tanpa markdown:"""

        try:
            raw = await llm_extract_fn(prompt)
            memories_data = _extract_json_array(raw)

            # PENJAGA: model kecil sering menyalin contoh few-shot mentah-mentah.
            # Buang apa pun yang mengandung jejak contoh, dan buang fakta yang
            # kata-katanya tidak muncul sama sekali di percakapan asli.
            memories_data = [
                m for m in memories_data
                if isinstance(m, dict)
                and m.get("content")
                and not _has_speaker_label(m["content"])
                and not _echoes_example(m["content"])
                and not _echoes_input(m["content"], conversation_summary)
                and _grounded_in(m["content"], conversation_summary)
            ]

            if not memories_data:
                logger.info("auto_extract: tidak ada memories yang di-extract.")
                return []

            return await self.save_many(
                memories=memories_data,
                conversation_id=conversation_id,
            )

        except Exception as e:
            logger.warning(f"auto_extract_and_save failed: {e}", exc_info=True)
            return []


# ── Penjaga hasil ekstraksi ───────────────────────────────────────────────────

# Jejak dari contoh few-shot. Kalau muncul di hasil, berarti model menyalin.
_EXAMPLE_TRACES = ("mochi", "kucing oranye")


def _echoes_example(content: str) -> bool:
    low = content.lower()
    return any(t in low for t in _EXAMPLE_TRACES)


def _has_speaker_label(content: str) -> bool:
    """
    Hasil ekstraksi tidak boleh berisi label pembicara.

    "User: ..." atau "Assistant: ..." adalah penanda bahwa model menyalin
    baris percakapan, bukan menyarikan fakta darinya. Ini aturan tegas —
    tidak ada kasus sah di mana sebuah fakta diawali label pembicara.
    """
    return bool(re.match(r"^\s*(user|assistant|pengguna|asisten)\s*:", content, re.IGNORECASE))


def _echoes_input(content: str, source: str) -> bool:
    """
    Tolak hasil yang hanya menyalin ulang masukan.

    Model kecil kadang tidak mengekstrak apa pun — ia sekadar memantulkan
    kembali kalimat pengguna, lengkap dengan awalan "User:". Itu bukan fakta,
    dan menyimpannya membuat memori penuh oleh salinan percakapan.
    """
    def bersih(s: str) -> str:
        s = re.sub(r"^\s*(user|assistant)\s*:\s*", "", s, flags=re.IGNORECASE)
        return re.sub(r"[^\w\s]", "", s).lower().strip()

    c, s = bersih(content), bersih(source)
    if not c:
        return True
    # Persis sama, atau isi memori termuat utuh di dalam masukan dengan
    # panjang yang hampir sama → itu pantulan, bukan ekstraksi.
    return c == s or (c in s and len(c) > len(s) * 0.7)


def _grounded_in(content: str, source: str) -> bool:
    """
    Cek apakah fakta benar-benar berakar di percakapan.

    Model kecil kadang mengarang nama atau tempat yang tidak pernah disebut.
    Kita ambil kata-kata bermakna dari fakta (bukan kata sambung), lalu minta
    sebagian besar (70%) benar-benar muncul di percakapan asli. Ambang segini
    menyaring karangan tapi masih memaafkan parafrase wajar — satu kata beda
    boleh, dua kata karangan langsung ditolak.
    """
    import math

    stop = {
        "user", "adalah", "yang", "dan", "di", "ke", "dari", "untuk", "dengan",
        "pada", "itu", "ini", "sebagai", "bahwa", "atau", "juga", "sudah",
        "telah", "akan", "bisa", "dapat", "saya", "kamu", "gua", "lo", "nama",
    }
    src = source.lower()
    words = [w.strip(".,!?:;()\"'").lower() for w in content.split()]
    meaningful = [w for w in words if len(w) > 3 and w not in stop]
    if not meaningful:
        return True
    hits = sum(1 for w in meaningful if w in src)
    return hits >= math.ceil(len(meaningful) * 0.7)


# ── tsquery helper ────────────────────────────────────────────────────────────

def _sanitize_tsquery_token(token: str) -> str:
    """
    Bersihkan satu token untuk dipakai di to_tsquery.

    to_tsquery sensitif terhadap karakter khusus (& | ! ( ) : * < >). Kalau
    query user mengandung ini mentah-mentah, bisa syntax error. Kita buang
    semua non-alphanumeric (kecuali internal, biar 'asyncpg' tetap utuh),
    lowercase, biar aman dan konsisten dengan config 'simple'.
    """
    import re
    # Ambil hanya huruf/angka (unicode-aware), buang sisanya
    cleaned = re.sub(r"[^\w]", "", token, flags=re.UNICODE)
    return cleaned.lower().strip()


# ── JSON extraction helper ────────────────────────────────────────────────────

def _extract_json_array(raw: str) -> list[dict]:
    """
    Robust JSON array extractor — toleran terhadap semua format LLM output.

    Steps:
    1. Strip markdown code fences (```json ... ```)
    2. Cari bracket [ ... ] pertama dan terakhir
    3. Parse JSON dari kandidat
    4. Gagal → return []

    Tidak pernah throw exception.
    """
    import json

    if not raw or not raw.strip():
        return []

    # Step 1: Strip markdown fences
    cleaned = re.sub(r"```(?:json)?\s*\n?", "", raw)
    cleaned = re.sub(r"\n?```", "", cleaned)
    cleaned = cleaned.strip()

    # Step 2: Cari bracket
    bracket_start = cleaned.find("[")
    bracket_end = cleaned.rfind("]")

    if bracket_start == -1 or bracket_end <= bracket_start:
        return []

    candidate = cleaned[bracket_start:bracket_end + 1]

    # Step 3: Parse
    try:
        result = json.loads(candidate)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        logger.warning(f"_extract_json_array: parse failed. Raw: {raw[:200]!r}")

    return []