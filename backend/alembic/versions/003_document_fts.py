"""add GIN index untuk FTS di document_chunks (RAG hybrid search)

Revision ID: 003_document_fts
Revises: 002_hybrid_search
Create Date: 2026-08-02

RAG search pakai hybrid (vector + keyword) sama seperti memory. Keyword
component butuh FTS index di document_chunks.content biar cepat.

Beda dengan memories (yang pakai generated tsvector column), di sini kita
pakai expression index langsung ke to_tsvector(content) — lebih simpel karena
document_chunks.content immutable setelah ingest (nggak pernah di-update).
"""

from alembic import op

revision = "003_document_fts"
down_revision = "002_hybrid_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Expression index untuk FTS di content
    op.execute("""
        CREATE INDEX idx_document_chunks_content_fts
        ON document_chunks
        USING GIN (to_tsvector('simple', content))
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_document_chunks_content_fts")