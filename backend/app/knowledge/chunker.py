"""
Text Chunker — Pecah dokumen jadi chunk untuk RAG.

Strategi: FIXED-SIZE dengan overlap.
- ~500 token per chunk (sweet spot untuk retrieval quality)
- Overlap ~75 token antar chunk (biar konteks di batas chunk tidak putus)

Kenapa fixed-size dulu (bukan semantic)?
- Simpel, predictable, cepat — cocok untuk fondasi
- Upgrade path ke semantic chunking gampang: interface Chunker didesain
  modular, tinggal swap implementasi (pola sama seperti LLM provider).

CATATAN token counting:
- Kita pakai APPROKSIMASI kata≈token (bukan tokenizer asli) biar tidak perlu
  dependency tiktoken yang berat. Rasio ~1.3 token per kata untuk teks campuran
  Indonesia-Inggris. Cukup akurat untuk chunking (tidak butuh presisi tokenizer).
"""

import re
from dataclasses import dataclass, field

# Target ukuran chunk dalam token (perkiraan)
# Batas keras: mxbai-embed-large hanya menerima 512 token per masukan.
# Kita bidik jauh di bawahnya karena ukuran di sini hanyalah PERKIRAAN
# (kata × rasio), bukan hitungan tokenizer asli. Teks teknis dengan banyak
# tanda baca, URL, atau potongan kode bisa menghasilkan token jauh lebih
# banyak dari perkiraan — pernah tercatat 825 token untuk target 500.
# 280 memberi ruang aman sekitar 45%.
EMBED_TOKEN_LIMIT = 512
DEFAULT_CHUNK_TOKENS = 280
# Overlap agar konteks di batas potongan tidak terputus
DEFAULT_OVERLAP_TOKENS = 45
# Rasio token per kata (aproksimasi untuk teks campuran ID-EN).
# Sengaja dilebihkan supaya perkiraan cenderung berhati-hati.
TOKENS_PER_WORD = 1.8

# Batas keras berbasis karakter. Dipakai sebagai jaring terakhir karena
# perkiraan berbasis kata tidak dapat diandalkan untuk teks padat simbol.
MAX_CHUNK_CHARS = 1200


@dataclass
class Chunk:
    """Satu chunk hasil pemecahan dokumen."""
    content: str
    index: int
    metadata: dict = field(default_factory=dict)


def _estimate_tokens(text: str) -> int:
    """Perkirakan jumlah token dari jumlah kata."""
    words = len(text.split())
    return int(words * TOKENS_PER_WORD)


def _split_sentences(text: str) -> list[str]:
    """
    Split teks jadi kalimat. Dipakai biar chunk boundary jatuh di akhir
    kalimat (bukan tengah kalimat) — lebih natural untuk retrieval.

    Pakai regex sederhana: split di . ! ? diikuti spasi/newline.
    Handle juga newline ganda (paragraph break).
    """
    # Split di paragraph break dulu (double newline)
    paragraphs = re.split(r"\n\s*\n", text)

    sentences = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Split per kalimat dalam paragraph
        parts = re.split(r"(?<=[.!?])\s+", para)
        sentences.extend(p.strip() for p in parts if p.strip())
    return sentences


class FixedSizeChunker:
    """
    Chunker fixed-size dengan overlap, sentence-aware.

    "Sentence-aware" artinya: kita nggak motong di tengah kalimat. Chunk
    di-build dengan nambahin kalimat sampai mendekati target size, baru
    pindah chunk baru. Overlap dibuat dengan carry-over beberapa kalimat
    terakhir ke chunk berikutnya.

    Usage:
        chunker = FixedSizeChunker()
        chunks = chunker.chunk(text, base_metadata={"page": 1})
    """

    def __init__(
        self,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ):
        self.chunk_tokens = chunk_tokens
        self.overlap_tokens = overlap_tokens

    def chunk(self, text: str, base_metadata: dict | None = None) -> list[Chunk]:
        """
        Pecah teks jadi list of Chunk.

        Args:
            text: teks dokumen
            base_metadata: metadata yang di-attach ke tiap chunk
                          (misal {"source": "file.pdf", "page": 1})
        """
        base_metadata = base_metadata or {}

        if not text.strip():
            return []

        sentences = _split_sentences(text)
        if not sentences:
            return []

        chunks: list[Chunk] = []
        current: list[str] = []
        current_tokens = 0
        chunk_index = 0

        for sentence in sentences:
            sent_tokens = _estimate_tokens(sentence)

            # Kalau satu kalimat aja udah lebih besar dari chunk size,
            # jadikan chunk sendiri (edge case: kalimat super panjang)
            if sent_tokens > self.chunk_tokens:
                # Flush current dulu
                if current:
                    chunks.append(self._make_chunk(current, chunk_index, base_metadata))
                    chunk_index += 1
                    current = []
                    current_tokens = 0
                # Kalimat panjang jadi chunk sendiri
                chunks.append(self._make_chunk([sentence], chunk_index, base_metadata))
                chunk_index += 1
                continue

            # Kalau nambah kalimat ini bakal exceed target, flush chunk dulu
            if current_tokens + sent_tokens > self.chunk_tokens and current:
                chunks.append(self._make_chunk(current, chunk_index, base_metadata))
                chunk_index += 1

                # Buat overlap: carry-over kalimat terakhir ke chunk baru
                current = self._build_overlap(current)
                current_tokens = sum(_estimate_tokens(s) for s in current)

            current.append(sentence)
            current_tokens += sent_tokens

        # Flush sisa
        if current:
            chunks.append(self._make_chunk(current, chunk_index, base_metadata))

        return chunks

    def _make_chunk(self, sentences: list[str], index: int, base_meta: dict) -> Chunk:
        content = " ".join(sentences)
        # Penjaga keras berbasis KARAKTER, bukan kata.
        #
        # Hitungan kata menyesatkan untuk markdown padat: tabel, blok kode,
        # dan URL memecah jadi banyak token per "kata". Batas karakter lebih
        # jujur — 1200 karakter aman di bawah 512 token bahkan untuk teks
        # yang penuh simbol.
        if len(content) > MAX_CHUNK_CHARS:
            potong = content[:MAX_CHUNK_CHARS]
            # Putuskan di batas kata terakhir supaya tidak memotong di tengah kata
            spasi = potong.rfind(" ")
            content = potong[:spasi] if spasi > MAX_CHUNK_CHARS * 0.8 else potong
        meta = dict(base_meta)
        meta["chunk_tokens_est"] = _estimate_tokens(content)
        return Chunk(content=content, index=index, metadata=meta)

    def _build_overlap(self, sentences: list[str]) -> list[str]:
        """
        Ambil beberapa kalimat terakhir dari chunk sebelumnya untuk overlap.
        Nambahin kalimat dari belakang sampai mendekati overlap_tokens.
        """
        overlap: list[str] = []
        tokens = 0
        for sentence in reversed(sentences):
            sent_tokens = _estimate_tokens(sentence)
            if tokens + sent_tokens > self.overlap_tokens:
                break
            overlap.insert(0, sentence)
            tokens += sent_tokens
        return overlap