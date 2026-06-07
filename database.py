from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from config import settings


def _make_engine():
    url = settings.DATABASE_URL
    if not url or not url.startswith(("postgresql://", "postgresql+asyncpg://")):
        return None
    url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    try:
        return create_async_engine(url, echo=False)
    except Exception:
        return None


engine = _make_engine()


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # Safe column additions for tables that already exist in production
        await conn.execute(text(
            "ALTER TABLE music_tracks ADD COLUMN IF NOT EXISTS drive_file_id TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE music_tracks ADD COLUMN IF NOT EXISTS audio_url TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE productions ADD COLUMN IF NOT EXISTS video_download_url TEXT"
        ))


@asynccontextmanager
async def get_session():
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
