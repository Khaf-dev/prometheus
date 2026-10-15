"""
Memory Extractor — Auto-simpan fakta penting dari percakapan (write path).

Ini "jaring pengaman" untuk masalah write path: model kecil (llama3.1:8b)
sering tidak proaktif memanggil memory_write, jadi info penting hilang.
Extractor ini jalan OTOMATIS setelah tiap turn selesai.

Design:
- Quick filter dulu (skip turn yang tidak informatif) → hemat LLM call
- LLM-based extraction via Ollama (pinter nangkep konteks)
- Async fire-and-forget: dipanggil SETELAH response dikirim ke user,
  jadi tidak nambah latency yang user rasakan

CATATAN SCOPE: Ini "light extract" — extraction ringan untuk semantic facts
(nama, pekerjaan, preferensi, keputusan). Auto-extract LENGKAP (full episodic
+ semantic dari seluruh conversation history) tetap di Phase 3.
"""

import logging
import os

import httpx

from app.memory.manager import MemoryManager

logger = logging.getLogger(__name__)

# Turn yang lebih pendek dari ini dianggap tidak informatif (skip extraction)
MIN_USER_CHARS = 15

# Kata-kata yang menandakan turn tidak informatif (filler/ack)
SKIP_PATTERNS = {
    "ok", "oke", "okay", "sip", "siap", "thanks", "thank you", "makasih",
    "lanjut", "gas", "kuy", "yaudah", "yaudh", "y", "ya", "iya", "nggak",
    "gak", "engga", "test", "tes", "coba", "hmm", "wkwk", "wkwkwk", "haha",
}


def _is_informative(user_message: str) -> bool:
    """
    Quick filter — return False kalau turn tidak layak di-extract.
    Hemat LLM call untuk pesan-pesan filler.
    """
    text = user_message.strip().lower()

    if len(text) < MIN_USER_CHARS:
        return False

    # Kalau seluruh pesan cuma filler word
    if text in SKIP_PATTERNS:
        return False

    # Kalau pesan cuma 1-2 kata pendek
    words = text.split()
    if len(words) <= 2 and all(w in SKIP_PATTERNS for w in words):
        return False

    # Skip PERTANYAAN — fakta datang dari pernyataan, bukan pertanyaan.
    # "Prometheus dibangun pakai apa?" itu pertanyaan, bukan fakta yang perlu
    # disimpan. Kalau di-extract, memory jadi kotor berisi pertanyaan user.
    if _is_question(text):
        return False

    return True


# Kata tanya (Indonesia + Inggris) di awal kalimat = indikator pertanyaan
_QUESTION_STARTERS = {
    "apa", "apakah", "siapa", "kapan", "kenapa", "mengapa", "gimana",
    "bagaimana", "dimana", "di mana", "kemana", "berapa", "mana",
    "what", "who", "when", "why", "how", "where", "which", "is", "are",
    "do", "does", "can", "could", "would", "should",
}


# Penanda permintaan/perintah. Ini BUKAN pertanyaan (tidak diakhiri "?"),
# tapi juga bukan pernyataan fakta — jadi tidak layak jadi memori.
# Contoh yang lolos sebelum ini ada: "kamu bisa cari dari dokumen yang..."
_REQUEST_MARKERS = (
    "kamu bisa", "bisa ga", "bisa gak", "bisanya", "tolong", "coba ", "cobain",
    "carikan", "cariin", "buatkan", "buatin", "kasih ", "kasi ",
    "sebutkan", "tunjukkan", "tunjukin", "berikan", "cek ", "lihat ",
    "jelasin", "jelaskan", "ringkas", "rangkum", "bantu ", "bantuin",
    "can you", "could you", "please ", "help me", "show me", "tell me",
)


def _is_question(text: str) -> bool:
    """
    Deteksi pesan yang meminta sesuatu — pertanyaan maupun perintah.

    Fakta datang dari PERNYATAAN. "Nama gua Kaff" itu fakta;
    "carikan dokumen gua" itu permintaan, dan menyimpannya
    hanya mengotori memori.
    """
    text = text.strip().lower()
    if text.endswith("?"):
        return True

    words = text.split()
    if words and words[0] in _QUESTION_STARTERS:
        return True

    # Penanda permintaan boleh muncul di mana saja pada bagian awal kalimat
    awal = " ".join(words[:6])
    if any(m in awal for m in _REQUEST_MARKERS):
        return True

    return False


async def _ollama_extract_fn(prompt: str) -> str:
    """
    LLM call ke Ollama untuk extraction.
    Pakai model default yang sama dengan chat (llama3.1:8b).
    """
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_DEFAULT_MODEL", "llama3.1:8b")

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                # Temperature rendah biar output JSON konsisten
                # Ekstraksi harus mengeluarkan JSON murni. Mode bernalar
                # menambah ratusan token yang tidak dipakai — dan pada model
                # tertentu, isi penalarannya bisa bocor ke keluaran.
                "think": False,
                "options": {"temperature": 0.1},
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("message", {}).get("content", "")


async def extract_and_save_memories(
    memory_manager: MemoryManager,
    user_message: str,
    assistant_message: str,
    conversation_id: str | None = None,
) -> list[str]:
    """
    Extract fakta penting dari satu turn percakapan dan simpan ke memory.

    Dipanggil async (fire-and-forget) setelah response dikirim ke user.

    Args:
        memory_manager: instance MemoryManager
        user_message: pesan terakhir dari user
        assistant_message: response dari assistant
        conversation_id: untuk audit trail

    Returns:
        list of saved memory IDs (kosong kalau tidak ada yang di-extract)
    """
    # Quick filter — skip turn tidak informatif
    if not _is_informative(user_message):
        logger.debug(f"Memory extract skipped (not informative): {user_message[:40]!r}")
        return []

    # PENTING: Fakta tentang USER harus datang dari apa yang USER katakan,
    # BUKAN dari jawaban assistant. Kalau kita ikut sertakan jawaban assistant
    # sebagai sumber fakta, extractor bisa salah tangkap — misal agent bilang
    # "Saya Prometheus, AI milik Anda" lalu tersimpan jadi memory palsu
    # "Nama user adalah Prometheus" atau "User adalah AI assistant".
    #
    # Jadi: kita HANYA kirim user_message sebagai sumber extraction. Assistant
    # message tidak disertakan sama sekali untuk menghindari kontaminasi.
    conversation = f"User: {user_message}"

    try:
        saved_ids = await memory_manager.auto_extract_and_save(
            conversation_summary=conversation,
            llm_extract_fn=_ollama_extract_fn,
            conversation_id=conversation_id,
        )
        if saved_ids:
            logger.info(f"Memory extract: {len(saved_ids)} memories saved from turn")
        return saved_ids
    except Exception as e:
        logger.warning(f"extract_and_save_memories failed: {e}")
        return []