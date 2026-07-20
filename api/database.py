from collections.abc import AsyncIterator
from urllib.parse import urlsplit, urlunsplit
import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


def _to_async_sqlalchemy_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme
    if scheme == "postgresql":
        scheme = "postgresql+psycopg"
    return urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))


class Base(DeclarativeBase):
    pass


engine = create_async_engine(_to_async_sqlalchemy_url(DATABASE_URL))

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
)


async def init_models() -> None:
    # Imports Chat/Message so they register on Base.metadata before create_all runs.
    import api.orm  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
