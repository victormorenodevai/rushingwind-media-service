from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlmodel import SQLModel

from config import settings


def _make_engine():
    url = settings.DATABASE_URL
    if not url:
        return None
    # Railway injects postgresql://, asyncpg requires postgresql+asyncpg://
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return create_async_engine(url, echo=False)


engine = _make_engine()


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


@asynccontextmanager
async def get_session():
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
