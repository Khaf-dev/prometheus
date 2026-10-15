"""
Prometheus — Complete Database Models
Semua core entities sesuai SRS data model, extended dengan SQLAlchemy best practices.

Design decisions:
- UUID primary keys untuk semua tabel utama (future-proof untuk multi-device sync)
- TIMESTAMPTZ (timezone-aware) untuk semua datetime
- JSONB untuk semi-structured data (tool_calls, metadata) — queryable, indexable
- pgvector VECTOR(1536) untuk embedding (OpenAI text-embedding-3-small dimensi)
- Soft delete via is_active flag, bukan hard delete
"""

import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


# ─── Auth ─────────────────────────────────────────────────────────────────────

class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    key_preview: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


# ─── Conversations & Messages ─────────────────────────────────────────────────

class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)           # auto-generated dari pesan pertama
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)  # provider default conversation ini
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", cascade="all, delete-orphan", lazy="select"
    )

    __table_args__ = (
        Index("ix_conversations_updated_at", "updated_at"),  # untuk sort recent conversations
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)    # user | assistant | tool | system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)    # agent tool invocations
    tool_results: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # hasil tool execution
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens_input: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_output: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        Index("ix_messages_conversation_id", "conversation_id"),
        Index("ix_messages_created_at", "created_at"),
    )


# ─── Long-Term Memory ─────────────────────────────────────────────────────────

class Memory(Base):
    """
    Long-term memory Prometheus.

    Dua tipe:
    - episodic: kejadian/event ("deploy production gagal tgl 10 Juni")
    - semantic: fakta stabil ("Kaff pakai FastAPI + React di SahamBagus")

    is_local_only = True → embedding tidak pernah dikirim ke cloud LLM sebagai context.
    Hanya dipakai untuk retrieval lokal.
    """
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    type: Mapped[str] = mapped_column(String(20), nullable=False)           # episodic | semantic
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    source_conversation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    is_local_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    __table_args__ = (
        Index("ix_memories_type", "type"),
        Index("ix_memories_is_local_only", "is_local_only"),
        # Vector index dibuat terpisah di migration (HNSW, bukan btree)
    )


# ─── Knowledge Base ───────────────────────────────────────────────────────────

class Document(Base):
    """
    Dokumen yang di-ingest ke knowledge base.
    Source bisa: PDF, URL, GitHub repo, Notion page, local file.
    """
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)    # pdf | url | github | notion | file
    source_uri: Mapped[str | None] = mapped_column(Text, nullable=True)     # original URL/path — untuk re-ingest
    namespace: Mapped[str | None] = mapped_column(String(100), nullable=True)  # "project:prometheus", "topic:odoo"
    status: Mapped[str] = mapped_column(String(20), default="processing", nullable=False)  # processing | ready | failed
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)  # kalau status=failed
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan", lazy="select"
    )

    __table_args__ = (
        Index("ix_documents_namespace", "namespace"),
        Index("ix_documents_status", "status"),
        Index("ix_documents_source_type", "source_type"),
    )


class DocumentChunk(Base):
    """
    Chunk dari dokumen — unit dasar untuk RAG retrieval.
    Setiap chunk punya embedding-nya sendiri.
    """
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_meta: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)  # page_num, section, etc.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    document: Mapped["Document"] = relationship("Document", back_populates="chunks")

    __table_args__ = (
        Index("ix_document_chunks_document_id", "document_id"),
        Index("ix_document_chunks_chunk_index", "document_id", "chunk_index"),
        # Vector HNSW index dibuat terpisah di migration
    )


# ─── Tasks & Reminders ────────────────────────────────────────────────────────

class Task(Base):
    """
    Tasks dan reminders yang dibuat via natural language conversation.
    Sync dua arah dengan Google Calendar via external_calendar_id.
    """
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal", nullable=False)  # low | normal | high
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)   # pending | done | cancelled
    source_conversation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    external_calendar_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # Google Calendar event ID
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    __table_args__ = (
        Index("ix_tasks_status", "status"),
        Index("ix_tasks_due_at", "due_at"),
    )


# ─── Tool Execution Logs ──────────────────────────────────────────────────────

class ToolLog(Base):
    """
    Audit log setiap kali agent memanggil tool.
    Penting untuk: debugging, observability, security audit.
    "Setiap kali data dikirim ke external service, dicatat" — NFR-002.
    """
    __tablename__ = "tool_logs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    conversation_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    input: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    output: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)   # success | error | timeout
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    __table_args__ = (
        Index("ix_tool_logs_conversation_id", "conversation_id"),
        Index("ix_tool_logs_tool_name", "tool_name"),
        Index("ix_tool_logs_executed_at", "executed_at"),
    )