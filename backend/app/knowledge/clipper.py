"""
Web Clipper — simpan halaman web ke knowledge base (Phase 3c).

Menumpang dua hal yang sudah ada:
- ekstraksi teks dari app.tools.implementations.browser (Phase 2)
- pipeline parse→chunk→embed→store dari IngestionService (Phase 3a)

Jadi tidak ada mesin baru di sini: clipper hanya pintu masuk lain ke
pipeline yang sama. Bedanya cuma sumbernya — URL, bukan berkas unggahan.
"""

import logging
import re
import uuid

import httpx
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.chunker import FixedSizeChunker
from app.memory.embeddings import EmbeddingService
from app.tools.implementations.browser import USER_AGENT, _extract_text

logger = logging.getLogger(__name__)

# Artikel bisa panjang; batas untuk unggahan berkas terlalu ketat di sini.
# Angka ini menahan halaman yang tidak masuk akal panjangnya, bukan artikel wajar.
MAX_CLIP_CHARS = 60_000


class ClipError(Exception):
    """Gagal mengambil atau membaca halaman."""


def _extract_title(html: str, url: str) -> str:
    """Ambil judul halaman; jatuh ke nama path kalau tidak ada."""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        if title:
            return title[:200]
    # Fallback: potongan terakhir URL yang bermakna
    tail = url.rstrip("/").split("/")[-1] or url
    return tail[:200]


async def _fetch(url: str, render_js: bool) -> tuple[str, str]:
    """Ambil halaman, kembalikan (judul, teks bersih)."""
    if render_js:
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise ClipError(
                "Playwright belum terpasang. Jalankan: "
                "pip install playwright && playwright install chromium"
            )
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                page = await browser.new_page(user_agent=USER_AGENT)
                await page.goto(url, wait_until="networkidle", timeout=30000)
                html = await page.content()
            finally:
                await browser.close()
    else:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=25.0, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text

    text = _extract_text(html, max_chars=MAX_CLIP_CHARS)
    if not text.strip():
        raise ClipError(
            "Halaman tidak menghasilkan teks. Mungkin butuh render JavaScript — "
            "coba lagi dengan render_js aktif."
        )
    return _extract_title(html, url), text


class ClipperService:
    """
    Simpan satu halaman web ke knowledge base.

    Usage:
        service = ClipperService(db, embedding_service)
        hasil = await service.clip("https://contoh.com/artikel")
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

    async def clip(
        self,
        url: str,
        namespace: str | None = None,
        render_js: bool = False,
    ) -> dict:
        if not url.startswith(("http://", "https://")):
            return {
                "document_id": None, "title": url, "status": "failed",
                "chunk_count": 0, "error": "URL harus diawali http:// atau https://",
            }

        doc_id = str(uuid.uuid4())
        title = url  # sementara, diganti setelah halaman terbaca

        try:
            title, body = await _fetch(url, render_js)

            # Catat dokumen dulu supaya kegagalan pun terlihat di daftar
            await self._db.execute(
                sql_text("""
                    INSERT INTO documents
                        (id, title, source_type, source_uri, namespace, status,
                         chunk_count, created_at, updated_at)
                    VALUES (:id, :title, 'url', :uri, :ns, 'processing', 0, NOW(), NOW())
                """),
                {"id": doc_id, "title": title, "uri": url, "ns": namespace},
            )
            await self._db.commit()

            chunks = self._chunker.chunk(
                body,
                base_metadata={"source_type": "url", "source_uri": url, "title": title},
            )
            if not chunks:
                raise ClipError("Halaman terbaca tapi tidak menghasilkan potongan.")

            embeddings = await self._embed_svc.embed_many([c.content for c in chunks])

            import json
            for chunk, emb in zip(chunks, embeddings):
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
            await self._db.commit()

            logger.info(f"Clipped '{title}': {len(chunks)} potongan dari {url}")
            return {
                "document_id": doc_id, "title": title, "status": "ready",
                "chunk_count": len(chunks), "error": None,
            }

        except Exception as e:
            msg = str(e)
            logger.warning(f"Clip gagal untuk {url}: {msg}")
            # Tandai gagal kalau dokumennya sempat tercatat
            await self._db.execute(
                sql_text("""
                    UPDATE documents SET status = 'failed', error_message = :err, updated_at = NOW()
                    WHERE id = :id
                """),
                {"err": msg[:500], "id": doc_id},
            )
            await self._db.commit()
            return {
                "document_id": doc_id, "title": title, "status": "failed",
                "chunk_count": 0, "error": msg,
            }