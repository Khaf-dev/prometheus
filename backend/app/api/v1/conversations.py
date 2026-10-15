"""
Conversations API — CRUD endpoints untuk conversation management.
Messages disimpan ke DB, bisa di-resume kapan saja.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated
from fastapi import Depends

from app.auth.middleware import AuthDep
from app.core.database import get_db_session
from app.core.models import Conversation, Message

router = APIRouter(prefix="/conversations", tags=["conversations"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]


# ── Schemas ───────────────────────────────────────────────────────────────────

class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    provider: str | None
    model: str | None
    tokens_input: int
    tokens_output: int
    cost_usd: float
    created_at: datetime


class ConversationOut(BaseModel):
    id: str
    title: str | None
    provider: str | None
    is_archived: bool
    message_count: int
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class CreateConversationRequest(BaseModel):
    title: str | None = None
    provider: str | None = None


class SaveMessageRequest(BaseModel):
    conversation_id: str
    role: str          # user | assistant | tool
    content: str
    provider: str | None = None
    model: str | None = None
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    tool_calls: dict | None = None
    tool_results: dict | None = None


class UpdateTitleRequest(BaseModel):
    title: str


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ConversationOut])
async def list_conversations(
    _: AuthDep,
    db: DbDep,
    limit: int = 50,
    include_archived: bool = False,
):
    """List semua conversations, sorted by latest activity."""
    query = select(
        Conversation,
        func.count(Message.id).label("message_count"),
    ).outerjoin(
        Message, Message.conversation_id == Conversation.id
    ).group_by(Conversation.id)

    if not include_archived:
        query = query.where(Conversation.is_archived == False)  # noqa: E712

    query = query.order_by(Conversation.updated_at.desc()).limit(limit)

    result = await db.execute(query)
    rows = result.all()

    return [
        ConversationOut(
            id=row.Conversation.id,
            title=row.Conversation.title,
            provider=row.Conversation.provider,
            is_archived=row.Conversation.is_archived,
            message_count=row.message_count,
            created_at=row.Conversation.created_at,
            updated_at=row.Conversation.updated_at,
        )
        for row in rows
    ]


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    request: CreateConversationRequest,
    _: AuthDep,
    db: DbDep,
):
    """Buat conversation baru."""
    now = datetime.now(timezone.utc)
    conv = Conversation(
        id=str(uuid.uuid4()),
        title=request.title,
        provider=request.provider,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    await db.flush()

    return ConversationOut(
        id=conv.id,
        title=conv.title,
        provider=conv.provider,
        is_archived=conv.is_archived,
        message_count=0,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.get("/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: str,
    _: AuthDep,
    db: DbDep,
):
    """Get conversation beserta semua messages-nya."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan.")

    msg_result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
    )
    messages = msg_result.scalars().all()

    return ConversationDetail(
        id=conv.id,
        title=conv.title,
        provider=conv.provider,
        is_archived=conv.is_archived,
        message_count=len(messages),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[
            MessageOut(
                id=m.id,
                role=m.role,
                content=m.content,
                provider=m.provider,
                model=m.model,
                tokens_input=m.tokens_input,
                tokens_output=m.tokens_output,
                cost_usd=float(m.cost_usd),
                created_at=m.created_at,
            )
            for m in messages
        ],
    )


@router.post("/messages", status_code=status.HTTP_201_CREATED)
async def save_message(
    request: SaveMessageRequest,
    _: AuthDep,
    db: DbDep,
):
    """
    Simpan satu message ke conversation.
    Dipanggil dari frontend setelah dapat response dari agent/LLM.
    """
    # Pastikan conversation exists
    result = await db.execute(
        select(Conversation).where(Conversation.id == request.conversation_id)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan.")

    now = datetime.now(timezone.utc)
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=request.conversation_id,
        role=request.role,
        content=request.content,
        provider=request.provider,
        model=request.model,
        tokens_input=request.tokens_input,
        tokens_output=request.tokens_output,
        cost_usd=request.cost_usd,
        tool_calls=request.tool_calls,
        tool_results=request.tool_results,
        created_at=now,
    )
    db.add(msg)

    # Update conversation updated_at + auto-generate title dari pesan pertama
    update_values: dict = {"updated_at": now}
    if conv.title is None and request.role == "user":
        # Auto-title dari 50 char pertama pesan user
        title = request.content[:50].strip()
        if len(request.content) > 50:
            title += "..."
        update_values["title"] = title

    await db.execute(
        update(Conversation)
        .where(Conversation.id == request.conversation_id)
        .values(**update_values)
    )

    await db.flush()
    return {"message_id": msg.id, "conversation_id": request.conversation_id}


@router.patch("/{conversation_id}/title")
async def update_title(
    conversation_id: str,
    request: UpdateTitleRequest,
    _: AuthDep,
    db: DbDep,
):
    """Update judul conversation secara manual."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan.")

    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(title=request.title, updated_at=datetime.now(timezone.utc))
    )
    return {"message": "Title updated"}


@router.patch("/{conversation_id}/archive")
async def archive_conversation(
    conversation_id: str,
    _: AuthDep,
    db: DbDep,
):
    """Archive conversation (soft delete)."""
    await db.execute(
        update(Conversation)
        .where(Conversation.id == conversation_id)
        .values(is_archived=True, updated_at=datetime.now(timezone.utc))
    )
    return {"message": "Archived"}


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    _: AuthDep,
    db: DbDep,
):
    """Hard delete conversation + semua messages-nya (CASCADE di DB)."""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Conversation tidak ditemukan.")

    await db.execute(
        delete(Conversation).where(Conversation.id == conversation_id)
    )