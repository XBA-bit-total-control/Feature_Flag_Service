from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.database import DATABASE_URL, get_async_session
from src.main import app
from src.models import Base, Flag, User
from src.models.user import UserRole


@pytest_asyncio.fixture
async def experiment_db(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[UUID, UUID, UUID]]:
    """Подготовить изолированную схему PostgreSQL и тестовых владельцев."""
    engine = create_async_engine(DATABASE_URL)
    schema = f"codex_experiments_{uuid4().hex}"
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.exec_driver_sql(f"CREATE SCHEMA {schema}")
                await connection.exec_driver_sql(f"SET LOCAL search_path TO {schema}")
                await connection.run_sync(Base.metadata.create_all)

                async def test_session() -> AsyncIterator[AsyncSession]:
                    async with AsyncSession(
                        bind=connection,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    ) as session:
                        try:
                            yield session
                            await session.commit()
                        except Exception:
                            await session.rollback()
                            raise

                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as session:
                    owner = User(email="owner@example.test", hashed_password="unused", role=UserRole.DEVELOPER)
                    other = User(email="other@example.test", hashed_password="unused", role=UserRole.DEVELOPER)
                    session.add_all([owner, other])
                    await session.flush()
                    flag = Flag(key="checkout", type="ab", owner_id=owner.id)
                    session.add(flag)
                    await session.commit()

                monkeypatch.setitem(
                    app.dependency_overrides, get_async_session, test_session
                )
                yield flag.id, owner.id, other.id
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def api_client(experiment_db: tuple[UUID, UUID, UUID]) -> AsyncIterator[httpx.AsyncClient]:
    """Создать HTTP-клиент для приложения с изолированной PostgreSQL."""
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        yield client
