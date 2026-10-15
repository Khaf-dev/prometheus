"""add tsvector column + GIN index untuk hybrid search

Revision ID: 002_hybrid_search
Revises: 001_initial
Create Date: 2026-08-01

Menambahkan full-text search capability ke tabel memories untuk hybrid search
(vector similarity + keyword BM25-style ranking via PostgreSQL FTS).

Kenapa perlu ini:
- Pure vector search punya blind spot untuk exact technical terms (nama library,
  error message, command spesifik). Contoh: query "asyncpg" mungkin meleset di
  vector space, tapi keyword match langsung nangkep.
- tsvector + GIN index = keyword search yang cepat, di-generate otomatis dari content.
"""

from alembic import op

revision = "002_hybrid_search"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Tambah kolom tsvector — auto-generated dari content
    #    'simple' config dipakai (bukan 'english') karena konten campuran
    #    Indonesia-Inggris. 'simple' tidak stem, jadi exact term match lebih reliable.
    op.execute("""
        ALTER TABLE memories
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
    """)

    # 2. GIN index untuk full-text search yang cepat
    op.execute("""
        CREATE INDEX idx_memories_content_tsv
        ON memories USING GIN (content_tsv)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_memories_content_tsv")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS content_tsv")