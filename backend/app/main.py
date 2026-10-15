"""
Prometheus Backend — FastAPI Application Entry Point
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

# ── Load .env SEBELUM import apapun yang baca os.getenv() ─────────────────────
# Ini WAJIB di paling atas — semua module (LLM Gateway, EmbeddingService, dll)
# baca API keys via os.getenv() saat class instantiation, jadi .env harus
# sudah ter-load sebelum module-module itu di-import.
from dotenv import load_dotenv

_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, chat, conversations, documents, conversations
from app.core.database import init_db
from app.core.dependencies import get_llm_gateway

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


async def _background_health_check():
    """Health check dijalankan di background — tidak blocking startup."""
    await asyncio.sleep(1)
    gateway = get_llm_gateway()
    health = await gateway.health_check_all()
    for provider, ok in health.items():
        icon = "✅" if ok else "❌"
        logger.info(f"  {icon} LLM Provider: {provider}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🔥 Prometheus starting up...")
    logger.info(f"  📄 .env loaded from: {_env_path}")

    await init_db()
    logger.info("  ✅ Database ready")

    get_llm_gateway()
    logger.info("  ✅ LLM Gateway initialized")

    asyncio.create_task(_background_health_check())

    logger.info("Prometheus ready 🚀")
    yield
    logger.info("Prometheus shutting down.")


app = FastAPI(
    title="Prometheus",
    description="Kaff's Agentic Framework for Intelligence",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:8081",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/v1")
app.include_router(chat.router, prefix="/api/v1")
app.include_router(conversations.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "service": "prometheus"}