"""
Database session management — async SQLAlchemy + asyncpg.
Semua models di-import di sini untuk Alembic autogenerate.
"""

import os
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Shared Base dari core models - satu-satunya source of truth
from app.core.models import Base

_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        database_url = os.getenv(
            "DATABASE_URL",
            "postgresql+asyncpg://prometheus:prometheus@localhost:5432/prometheus",
        )

        _engine = create_async_engine(
            database_url,
            echo=os.getenv("DEBUG", "false").lower() == "true",
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _engine



def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
    return _session_factory



async def init_db() -> None:
    """Buat semua tabel (intial setup). Gunakan Alembic untuk production migrations"""
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)



async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — inject async DB session ke route."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise