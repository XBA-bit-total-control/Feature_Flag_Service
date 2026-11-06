import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.database import DATABASE_URL, get_async_session
from src.main import app
from src.models import Base, Flag, User
from src.utils.jwt_handler import create_access_token


@pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка локальной PostgreSQL",
)
@pytest.mark.asyncio
async def test_experiment_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """API проверяет владельца, уникальность имени и переходы статусов."""
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
                    owner = User(email="owner@example.test", hashed_password="unused")
                    other = User(email="other@example.test", hashed_password="unused")
                    session.add_all([owner, other])
                    await session.flush()
                    flag = Flag(key="checkout", type="ab", owner_id=owner.id)
                    session.add(flag)
                    await session.commit()

                monkeypatch.setitem(
                    app.dependency_overrides, get_async_session, test_session
                )
                await _check_lifecycle(flag.id, owner.id, other.id)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _check_lifecycle(flag_id: UUID, owner_id: UUID, other_id: UUID) -> None:
    transport = httpx.ASGITransport(app=app)
    owner = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    other = {"Authorization": f"Bearer {create_access_token(str(other_id))}"}
    base = "/api/experiments"
    payload = {
        "flag_id": str(flag_id), "name": "Checkout", "primary_metric": "conversion"
    }

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async def request(method, path, expected, headers=owner, **kwargs):
            response = await client.request(method, path, headers=headers, **kwargs)
            assert response.status_code == expected, (
                f"{method} {path}: ожидался статус {expected}, "
                f"получено {response.status_code}, ответ {response.text}"
            )
            return response

        await request("GET", base, 401, headers={})
        await request("POST", base, 403, headers=other, json=payload)
        await request("POST", base, 404, json={**payload, "flag_id": str(uuid4())})
        created = (await request("POST", base, 201, json=payload)).json()
        assert created["status"] == "draft" and created["start_date"] is None, (
            f"Новый эксперимент должен быть черновиком без даты старта: {created}"
        )
        await request("POST", base, 409, json=payload)
        path = f"{base}/{created['id']}"
        await request("GET", path, 200)
        await request("GET", path, 403, headers=other)
        await request("PATCH", path, 403, headers=other, json={"status": "running"})
        await request("PATCH", path, 422, json={})
        await request("PATCH", path, 422, json={"min_sample_size": 2**31})
        await request("PATCH", path, 422, json={"status": "running", "name": "New"})
        updated = (await request("PATCH", path, 200, json={"min_sample_size": 100})).json()
        assert updated["min_sample_size"] == 100, (
            f"Ожидалась выборка 100, получено {updated['min_sample_size']}"
        )

        another = (await request("POST", base, 201, json={**payload, "name": "Next"})).json()
        another_path = f"{base}/{another['id']}"
        await request("PATCH", another_path, 409, json={"name": "Checkout"})
        await request("PATCH", path, 409, json={"status": "completed"})
        started = (await request("PATCH", path, 200, json={"status": "running"})).json()
        assert started["start_date"] is not None, "При запуске ожидается дата старта"
        await request("PATCH", path, 409, json={"alpha": 0.1})
        await request("DELETE", path, 409)
        await request("PATCH", path, 200, json={"status": "paused"})
        await request("PATCH", another_path, 409, json={"status": "running"})
        resumed = (await request("PATCH", path, 200, json={"status": "running"})).json()
        assert resumed["start_date"] == started["start_date"], (
            "Возобновление должно сохранять первоначальную дату старта"
        )

        listed = (await request(
            "GET", base, 200, params={"flag_id": str(flag_id), "status": "running"}
        )).json()
        assert [item["id"] for item in listed] == [created["id"]], (
            f"Фильтры должны вернуть только запущенный эксперимент: {listed}"
        )
        foreign_list = (await request("GET", base, 200, headers=other)).json()
        assert foreign_list == [], f"Список раскрыл чужие эксперименты: {foreign_list}"
        completed = (
            await request("PATCH", path, 200, json={"status": "completed"})
        ).json()
        repeated = (
            await request("PATCH", path, 200, json={"status": "completed"})
        ).json()
        assert completed["end_date"] and repeated["end_date"] == completed["end_date"], (
            "Повтор завершения должен сохранять дату окончания"
        )
        await request("PATCH", path, 409, json={"status": "running"})
        await request("DELETE", another_path, 204)
        await request("GET", another_path, 404)
