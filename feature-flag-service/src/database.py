from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from pymongo import MongoClient
from typing import AsyncGenerator

from src.config import settings


# --- PostgreSQL (async) ---

DATABASE_URL = (
    f"postgresql+asyncpg://{settings.POSTGRES_USER}:"
    f"{settings.POSTGRES_PASSWORD}@{settings.POSTGRES_HOST}:"
    f"{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency для получения асинхронной сессии PostgreSQL."""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# --- MongoDB (sync) ---

MONGO_URL = (
    f"mongodb://{settings.MONGO_HOST}:{settings.MONGO_PORT}/"
)

mongo_client = MongoClient(MONGO_URL)
mongo_database = mongo_client[settings.MONGO_DB]


def get_mongo_client() -> MongoClient:
    """FastAPI dependency для получения MongoDB клиента."""
    return mongo_client


def get_mongo_database() -> object:
    """FastAPI dependency для получения MongoDB базы данных."""
    return mongo_database
