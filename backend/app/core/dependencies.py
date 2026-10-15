"""
FastAPI dependency injection setup — semua deps terpusat di sini.
"""

from functools import lru_cache
from typing import Annotated, TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.llm.gateway import LLMGateway
from app.memory.embeddings import EmbeddingService

if TYPE_CHECKING:
    from app.memory.manager import MemoryManager
    from app.agent.graph import PrometheusAgent

@lru_cache(maxsize=1)
def get_llm_gateway() -> LLMGateway:
    """Singleton LLM Gateway."""
    return LLMGateway.from_env()


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    """Singleton Embedding Service."""
    return EmbeddingService.from_env()


def get_tool_registry(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    embedding_svc: Annotated[EmbeddingService, Depends(get_embedding_service)],
):
    """
    ToolRegistry per-request — butuh db session dan embedding service.
    Lazy import untuk hindari circular dependency.
    """
    from app.tools.registry import ToolRegistry
    from app.tools.implementations.memory_read import MemoryReadTool
    from app.tools.implementations.memory_write import MemoryWriteTool
    from app.tools.implementations.filesystem import FileReadTool, FileWriteTool, FileListTool
    from app.tools.implementations.github import (
        GitHubListPRsTool,
        GitHubGetPRTool,
        GitHubListIssuesTool,
        GitHubGetRepoTool,
        GitHubSearchCodeTool,
    )
    import os

    tools = [
        MemoryReadTool(db=db, embedding_fn=embedding_svc.embed),
        MemoryWriteTool(db=db, embedding_fn=embedding_svc.embed),
        FileReadTool(),
        FileWriteTool(),
        FileListTool(),
    ]

    # Register GitHub tools hanya kalau token tersedia
    if os.getenv("GITHUB_TOKEN"):
        tools.extend([
            GitHubListPRsTool(),
            GitHubGetPRTool(),
            GitHubListIssuesTool(),
            GitHubGetRepoTool(),
            GitHubSearchCodeTool(),
        ])

    # Register Google Calendar tools hanya kalau credentials tersedia
    if os.getenv("GOOGLE_CALENDAR_CREDENTIALS_FILE"):
        from app.tools.implementations.google_calendar import (
            CalendarListEventsTool,
            CalendarCreateEventTool,
            CalendarDeleteEventTool,
            CalendarCheckFreeBusyTool,
        )
        tools.extend([
            CalendarListEventsTool(),
            CalendarCreateEventTool(),
            CalendarDeleteEventTool(),
            CalendarCheckFreeBusyTool(),
        ])

    # Register Browser tools (no API key needed - selalu aktif)
    from app.tools.implementations.browser import (
        BrowserFetchTool,
        BrowserSearchTool,
        BrowserScreenshotTool,
    )
    tools.extend([
        BrowserFetchTool(),
        BrowserSearchTool(),
        BrowserScreenshotTool(),
    ])

    # Register RAG search tool (knowledge base - selalu aktif)
    from app.tools.implementations.rag_search import RAGSearchTool, KnowledgeListTool
    tools.extend([
        RAGSearchTool(db=db, embedding_fn=embedding_svc.embed),
        KnowledgeListTool(db=db),
    ])

    registry = ToolRegistry()
    registry.register_many(tools)
    return registry


# ── Type aliases untuk clean DI di routes ─────────────────────────────────────
LLMGatewayDep     = Annotated[LLMGateway,        Depends(get_llm_gateway)]
DbSessionDep      = Annotated[AsyncSession,       Depends(get_db_session)]
EmbeddingDep      = Annotated[EmbeddingService,   Depends(get_embedding_service)]


def get_memory_manager(
    db: Annotated[AsyncSession, Depends(get_db_session)],
    embedding_svc: Annotated[EmbeddingService, Depends(get_embedding_service)],
) -> "MemoryManager":
    from app.memory.manager import MemoryManager
    return MemoryManager(db=db, embedding_service=embedding_svc)


def get_prometheus_agent(
    gateway: Annotated[LLMGateway, Depends(get_llm_gateway)],
    tool_registry=Depends(get_tool_registry),
    memory_manager=Depends(get_memory_manager),
) -> "PrometheusAgent":
    from app.agent.graph import PrometheusAgent
    return PrometheusAgent(
        gateway=gateway,
        tool_registry=tool_registry,
        memory_manager=memory_manager,
    )


MemoryManagerDep = Annotated["MemoryManager", Depends(get_memory_manager)]
AgentDep        = Annotated["PrometheusAgent", Depends(get_prometheus_agent)]