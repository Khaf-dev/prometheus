"""
Kelola memory dari terminal — tanpa perlu docker exec.

Pakai koneksi yang sama dengan aplikasi (DATABASE_URL di .env), jadi kalau
backend bisa jalan, script ini juga bisa.

Contoh:
    python scripts/memory.py list
    python scripts/memory.py list --limit 50
    python scripts/memory.py find budi
    python scripts/memory.py delete-matching budi gojek
    python scripts/memory.py delete <memory-id>
    python scripts/memory.py purge          # hapus SEMUA (minta konfirmasi)
"""

import argparse
import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

import os  # noqa: E402  (harus setelah load_dotenv)

RAW_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://prometheus:prometheus@127.0.0.1:5432/prometheus",
)
# asyncpg tidak mengerti awalan "+asyncpg" milik SQLAlchemy
DSN = RAW_URL.replace("postgresql+asyncpg://", "postgresql://")


async def connect():
    import asyncpg
    try:
        return await asyncpg.connect(DSN)
    except Exception as e:
        print(f"❌ Tidak bisa terhubung ke database: {e}")
        print(f"   DSN: {DSN}")
        print("   Pastikan container postgres jalan: docker compose ps")
        sys.exit(1)


def show(rows) -> None:
    if not rows:
        print("   (kosong)")
        return
    for r in rows:
        when = r["created_at"].strftime("%d %b %H:%M")
        print(f"   [{r['type']:8}] {when}  {r['content']}")
        print(f"              id={r['id']}")


async def cmd_list(limit: int):
    conn = await connect()
    try:
        rows = await conn.fetch(
            "SELECT id, type, content, created_at FROM memories "
            "ORDER BY created_at DESC LIMIT $1",
            limit,
        )
        total = await conn.fetchval("SELECT COUNT(*) FROM memories")
        print(f"\n📚 {total} memory tersimpan (menampilkan {len(rows)}):\n")
        show(rows)
        print()
    finally:
        await conn.close()


async def cmd_find(term: str):
    conn = await connect()
    try:
        rows = await conn.fetch(
            "SELECT id, type, content, created_at FROM memories "
            "WHERE content ILIKE $1 ORDER BY created_at DESC",
            f"%{term}%",
        )
        print(f"\n🔍 {len(rows)} memory mengandung '{term}':\n")
        show(rows)
        print()
    finally:
        await conn.close()


async def cmd_delete_matching(terms: list[str]):
    conn = await connect()
    try:
        # Lihat dulu apa yang akan dihapus — jangan menghapus tanpa ditunjukkan
        clauses = " OR ".join(f"content ILIKE ${i + 1}" for i in range(len(terms)))
        params = [f"%{t}%" for t in terms]
        rows = await conn.fetch(
            f"SELECT id, type, content, created_at FROM memories WHERE {clauses}",
            *params,
        )
        if not rows:
            print(f"\n✓ Tidak ada memory yang cocok dengan {terms}. Tidak ada yang dihapus.\n")
            return

        print(f"\n🗑  Akan menghapus {len(rows)} memory:\n")
        show(rows)
        jawab = input("\nLanjutkan? (y/n): ").strip().lower()
        if jawab != "y":
            print("Dibatalkan.\n")
            return

        deleted = await conn.execute(
            f"DELETE FROM memories WHERE {clauses}", *params
        )
        print(f"✓ {deleted}\n")
    finally:
        await conn.close()


async def cmd_delete(memory_id: str):
    conn = await connect()
    try:
        row = await conn.fetchrow(
            "DELETE FROM memories WHERE id = $1 RETURNING content", memory_id
        )
        print(f"✓ Dihapus: {row['content']}" if row else "❌ ID tidak ditemukan")
    finally:
        await conn.close()


async def cmd_purge():
    conn = await connect()
    try:
        total = await conn.fetchval("SELECT COUNT(*) FROM memories")
        if total == 0:
            print("\n✓ Sudah kosong.\n")
            return
        print(f"\n⚠  Ini akan menghapus SEMUA {total} memory. Tidak bisa dibatalkan.")
        if input("Ketik 'HAPUS SEMUA' untuk melanjutkan: ").strip() != "HAPUS SEMUA":
            print("Dibatalkan.\n")
            return
        await conn.execute("DELETE FROM memories")
        print(f"✓ {total} memory dihapus.\n")
    finally:
        await conn.close()


def main():
    p = argparse.ArgumentParser(description="Kelola long-term memory Prometheus")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="Tampilkan memory terbaru")
    pl.add_argument("--limit", type=int, default=20)

    pf = sub.add_parser("find", help="Cari memory berdasarkan kata")
    pf.add_argument("term")

    pdm = sub.add_parser("delete-matching", help="Hapus memory yang mengandung kata tertentu")
    pdm.add_argument("terms", nargs="+")

    pd = sub.add_parser("delete", help="Hapus satu memory berdasarkan id")
    pd.add_argument("memory_id")

    sub.add_parser("purge", help="Hapus semua memory")

    a = p.parse_args()
    if a.cmd == "list":
        asyncio.run(cmd_list(a.limit))
    elif a.cmd == "find":
        asyncio.run(cmd_find(a.term))
    elif a.cmd == "delete-matching":
        asyncio.run(cmd_delete_matching(a.terms))
    elif a.cmd == "delete":
        asyncio.run(cmd_delete(a.memory_id))
    elif a.cmd == "purge":
        asyncio.run(cmd_purge())


if __name__ == "__main__":
    main()