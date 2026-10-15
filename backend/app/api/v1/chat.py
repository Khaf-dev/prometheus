"""
Chat endpoint — SSE streaming untuk dua mode:
1. Direct LLM (simple chat, cepat)
2. Agent mode (ReAct loop, dengan tool use dan step transparency)

Setiap chat otomatis disimpan ke DB (conversations + messages).
"""

import json
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Annotated

from app.auth.middleware import AuthDep
from app.core.database import get_db_session
from app.core.dependencies import LLMGatewayDep, AgentDep, MemoryManagerDep
from app.core.models import Conversation, Message
from app.llm.base import LLMConfig, Message as LLMMessage, ProviderName, TaskType
from app.memory.extractor import extract_and_save_memories

router = APIRouter(prefix="/agent", tags=["agent"])

DbDep = Annotated[AsyncSession, Depends(get_db_session)]


# ── Schemas ───────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    conversation_id: str | None = None   # kalau None → buat conversation baru
    provider: ProviderName | None = None
    task_type: TaskType = TaskType.GENERAL
    model: str | None = None
    system_prompt: str | None = None
    agent_mode: bool = False
    pillar: str | None = None       # wilayah aktif — menyaring tool yang dikirim
    focus_tool: str | None = None   # kemampuan yang diklik pengguna di peta bintang


class ProviderStatusResponse(BaseModel):
    name: str
    available: bool


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_or_create_conversation(
    db: AsyncSession,
    conversation_id: str | None,
    provider: str | None,
) -> Conversation:
    """Get existing conversation atau buat baru."""
    if conversation_id:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one_or_none()
        if conv:
            return conv

    # Buat baru
    now = datetime.now(timezone.utc)
    conv = Conversation(
        id=str(uuid.uuid4()),
        title=None,   # auto-generate dari pesan pertama nanti
        provider=provider,
        is_archived=False,
        created_at=now,
        updated_at=now,
    )
    db.add(conv)
    await db.flush()
    return conv


async def _save_message(
    db: AsyncSession,
    conversation_id: str,
    role: str,
    content: str,
    provider: str | None = None,
    model: str | None = None,
    tokens_input: int = 0,
    tokens_output: int = 0,
    cost_usd: float = 0.0,
) -> Message:
    """Simpan satu message ke DB."""
    msg = Message(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        role=role,
        content=content,
        provider=provider,
        model=model,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd=cost_usd,
        created_at=datetime.now(timezone.utc),
    )
    db.add(msg)
    await db.flush()
    return msg


async def _auto_title(conv: Conversation, first_user_message: str, db: AsyncSession):
    """Auto-generate title dari pesan pertama user (max 50 char)."""
    if conv.title:
        return
    title = first_user_message[:50].strip()
    if len(first_user_message) > 50:
        title += "..."
    conv.title = title
    conv.updated_at = datetime.now(timezone.utc)


# ── SSE helpers ───────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream_direct(
    gateway: LLMGatewayDep,
    messages: list[LLMMessage],
    config: LLMConfig,
    db: AsyncSession,
    conversation_id: str,
    memory_manager,
    user_message: str,
) -> AsyncIterator[str]:
    """Stream direct LLM response + save ke DB + extract memories."""
    full_response = ""
    provider_used = None
    usage_data = {}

    try:
        async for chunk in gateway.stream(messages, config):
            if chunk.is_done:
                usage_data = chunk.usage or {}
                yield _sse({
                    "type": "done",
                    "usage": usage_data,
                    "session_cost_usd": gateway.session_cost_usd,
                    "agent_mode": False,
                    "conversation_id": conversation_id,
                })
            else:
                full_response += chunk.delta
                provider_used = chunk.provider.value if chunk.provider else None
                yield _sse({
                    "type": "delta",
                    "delta": chunk.delta,
                    "provider": provider_used,
                })
    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})
        return

    # Save assistant response ke DB
    if full_response:
        await _save_message(
            db=db,
            conversation_id=conversation_id,
            role="assistant",
            content=full_response,
            provider=provider_used,
            model=usage_data.get("model"),
            tokens_input=usage_data.get("tokens_input", 0),
            tokens_output=usage_data.get("tokens_output", 0),
            cost_usd=usage_data.get("cost_usd", 0.0),
        )
        await db.commit()

        # Write path: auto-extract fakta penting dari turn ini
        await extract_and_save_memories(
            memory_manager=memory_manager,
            user_message=user_message,
            assistant_message=full_response,
            conversation_id=conversation_id,
        )


async def _stream_agent(
    agent: AgentDep,
    messages: list[dict],
    provider: str | None,
    task_type: str,
    conversation_id: str,
    db: AsyncSession,
    memory_manager,
    user_message: str,
    pillar: str | None = None,
    focus_tool: str | None = None,
) -> AsyncIterator[str]:
    """Stream agent ReAct execution + save ke DB + extract memories."""
    full_response = ""

    try:
        async for event in agent.stream_steps(
            messages=messages,
            conversation_id=conversation_id,
            provider=provider,
            task_type=task_type,
            pillar=pillar,
            focus_tool=focus_tool,
        ):
            if event["type"] == "step":
                yield _sse({
                    "type": "step",
                    "step": event["step"],
                    "agent_mode": True,
                })
            elif event["type"] == "response":
                full_response = event["content"]
                chunk_size = 10
                for i in range(0, len(full_response), chunk_size):
                    chunk = full_response[i:i + chunk_size]
                    yield _sse({
                        "type": "delta",
                        "delta": chunk,
                        "provider": provider or "ollama",
                    })
            elif event["type"] == "error":
                yield _sse({"type": "error", "message": event["message"]})
            elif event["type"] == "done":
                yield _sse({
                    "type": "done",
                    "iterations": event.get("iterations", 0),
                    "agent_mode": True,
                    "conversation_id": conversation_id,
                })
    except Exception as e:
        yield _sse({"type": "error", "message": str(e)})
        return

    # Save assistant response ke DB
    if full_response:
        await _save_message(
            db=db,
            conversation_id=conversation_id,
            role="assistant",
            content=full_response,
            provider=provider or "ollama",
        )
        await db.commit()

        # Write path: auto-extract fakta penting dari turn ini.
        # Dijalankan SETELAH response selesai di-stream ke user, jadi
        # tidak nambah latency yang user rasakan. Error di sini tidak
        # boleh ganggu response utama (di-swallow di dalam extractor).
        await extract_and_save_memories(
            memory_manager=memory_manager,
            user_message=user_message,
            assistant_message=full_response,
            conversation_id=conversation_id,
        )


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/chat")
async def chat(
    request: ChatRequest,
    gateway: LLMGatewayDep,
    agent: AgentDep,
    memory_manager: MemoryManagerDep,
    _: AuthDep,
    db: DbDep,
):
    """
    Chat endpoint dengan persistence ke DB.

    Flow:
    1. Get atau create conversation
    2. Save user message ke DB
    3. Stream response (direct atau agent mode)
    4. Save assistant response ke DB (di akhir stream)
    5. Auto-extract memories dari turn (write path)
    """
    # Get/create conversation
    conv = await _get_or_create_conversation(
        db=db,
        conversation_id=request.conversation_id,
        provider=request.provider.value if request.provider else None,
    )

    # Save user message
    last_user_content = ""
    for msg in request.messages:
        if msg.role == "user":
            last_user_content = msg.content

    if last_user_content:
        await _save_message(
            db=db,
            conversation_id=conv.id,
            role="user",
            content=last_user_content,
        )
        # Auto-generate title dari pesan pertama
        await _auto_title(conv, last_user_content, db)
        await db.commit()

    if request.agent_mode:
        messages_dict = [{"role": m.role, "content": m.content} for m in request.messages]
        stream = _stream_agent(
            agent=agent,
            messages=messages_dict,
            provider=request.provider.value if request.provider else None,
            task_type=request.task_type.value,
            conversation_id=conv.id,
            db=db,
            memory_manager=memory_manager,
            user_message=last_user_content,
            pillar=request.pillar,
            focus_tool=request.focus_tool,
        )
    else:
        messages = [LLMMessage(role=m.role, content=m.content) for m in request.messages]
        config = LLMConfig(
            provider=request.provider,
            task_type=request.task_type,
            model=request.model,
            system_prompt=request.system_prompt,
        )
        stream = _stream_direct(
            gateway, messages, config, db, conv.id,
            memory_manager=memory_manager,
            user_message=last_user_content,
            pillar=request.pillar,
            focus_tool=request.focus_tool,
        )

    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Conversation-Id": conv.id,
        },
    )


@router.get("/providers", response_model=list[ProviderStatusResponse])
async def get_providers(gateway: LLMGatewayDep, _: AuthDep):
    health = await gateway.health_check_all()
    return [
        ProviderStatusResponse(name=name, available=status)
        for name, status in health.items()
    ]


@router.post("/providers/{provider_name}/test")
async def test_provider(
    provider_name: ProviderName,
    gateway: LLMGatewayDep,
    _: AuthDep,
):
    health = await gateway.health_check_all()
    available = health.get(provider_name.value, False)
    return {
        "provider": provider_name.value,
        "available": available,
        "message": "OK" if available else "Provider tidak tersedia",
    }