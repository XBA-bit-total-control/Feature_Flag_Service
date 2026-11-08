import os
from collections.abc import AsyncIterator
from unittest.mock import Mock
from kombu.exceptions import OperationalError
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from pymongo import MongoClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.config import settings
from src.database import DATABASE_URL, get_async_session, get_mongo_database
from src.main import app
from src.models import Base, Flag, User
from src.utils.jwt_handler import create_access_token
from src.celery_app import app as celery_app
from src.services import event_service
from src.tasks import event_tasks
from src.api import resolve as resolve_api
from pymongo.errors import ServerSelectionTimeoutError


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POSTGRES_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка локальной PostgreSQL",
)


@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка MongoDB",
)
@pytest.mark.asyncio
async def test_resolve_impression_to_statistics(
    experiment_db: tuple[UUID, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Повторные показы и конверсии дают одного участника в статистике."""
    flag_id, owner_id, _ = experiment_db
    headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    publish = Mock(side_effect=OperationalError("broker unavailable"))
    monkeypatch.setattr(event_service.celery_app, "send_task", publish)
    with MongoClient(
        host=settings.MONGO_HOST, port=settings.MONGO_PORT, serverSelectionTimeoutMS=3000
    ) as mongo:
        database = mongo[f"codex_impressions_{uuid4().hex}"]
        monkeypatch.setitem(app.dependency_overrides, get_mongo_database, lambda: database)
        monkeypatch.setattr(event_tasks, "get_mongo_database", lambda: database)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                payload = {"flag_key": "checkout", "user_id": "u1"}
                disabled = await client.post("/resolve", json=payload)
                assert disabled.status_code == 200, f"Ошибка resolve: {disabled.text}"
                assert database.events.count_documents({}) == 0, (
                    "Выключенный флаг создал impression"
                )
                for key in ("A", "B"):
                    variant = await client.post(
                        "/api/flags/checkout/variants", headers=headers,
                        json={"flag_id": str(flag_id), "variant_key": key},
                    )
                    assert variant.status_code == 201, f"Не создан вариант: {variant.text}"
                experiment = await client.post("/api/experiments", headers=headers, json={
                    "flag_id": str(flag_id), "name": "Impressions", "primary_metric": "buy",
                })
                assert experiment.status_code == 201, f"Ошибка создания: {experiment.text}"
                path = f"/api/experiments/{experiment.json()['id']}"
                await client.patch(path, headers=headers, json={"status": "running"})
                await client.patch(
                    "/api/flags/checkout", headers=headers, json={"enabled": True}
                )
                first = await client.post("/resolve", json=payload)
                second = await client.post("/resolve", json=payload)
                assert first.status_code == second.status_code == 200, (
                    f"Сбой брокера повлиял на назначение: {first.text}, {second.text}"
                )
                result = first.json()
                shows = list(database.events.find({"event_type": "impression"}))
                assert len(shows) == 2 and all(
                    show["variant_key"] == result["variant"] for show in shows
                ), "Не сохранены два показа назначенного варианта"
                conversion = {
                    **payload, "experiment_id": result["experiment_id"],
                    "variant_key": result["variant"], "event_type": "conversion",
                    "event_name": "buy",
                }
                batch = await client.post(
                    "/api/events", json={"events": [conversion, conversion]}
                )
                assert batch.status_code == 201, f"Конверсии не приняты: {batch.text}"
                assert event_tasks.process_pending_events.run() == 4, (
                    "Воркер должен обработать два показа и две конверсии"
                )
                stats = await client.post(path + "/results", headers=headers)
                assert stats.status_code == 200, f"Расчёт не выполнен: {stats.text}"
                rows = stats.json()
                assert sum(row["sample_size"] for row in rows) == 1, (
                    f"Повторные показы увеличили число участников: {rows}"
                )
                assert sum(row["conversions"] for row in rows) == 1, (
                    f"Повторные конверсии увеличили число успехов: {rows}"
                )
                monkeypatch.setattr(
                    resolve_api, "save_event",
                    Mock(side_effect=ServerSelectionTimeoutError("internal host")),
                )
                failed = await client.post("/resolve", json=payload)
                assert failed.status_code == 503 and "internal host" not in failed.text, (
                    f"Некорректная обработка отказа MongoDB: {failed.text}"
                )
        finally:
            database.events.drop()


@pytest.mark.asyncio
async def test_resolve_api_with_experiment(
    experiment_db: tuple[UUID, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve проверяет доступность флага и связь с запущенным экспериментом."""
    flag_id, owner_id, _ = experiment_db
    monkeypatch.setattr(resolve_api, "save_event", Mock())
    monkeypatch.setattr(resolve_api, "schedule_processing", Mock())
    headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        async def resolve(key="checkout", **kwargs):
            return await client.post(
                "/resolve", json={"flag_key": key, "user_identifier": "u1", **kwargs}
            )

        disabled = await resolve()
        assert disabled.status_code == 200 and not disabled.json()["enabled"], (
            f"Выключенный флаг должен быть недоступен: {disabled.text}"
        )
        await client.patch("/api/flags/checkout", headers=headers, json={"enabled": True})
        inactive = await resolve()
        assert inactive.json()["reason"] == "no_active_experiment", (
            f"Флаг без запущенного эксперимента не должен назначать вариант: {inactive.text}"
        )
        for key in ("A", "B"):
            variant = await client.post(
                "/api/flags/checkout/variants", headers=headers,
                json={"flag_id": str(flag_id), "variant_key": key},
            )
            assert variant.status_code == 201, f"Вариант не создан: {variant.text}"
        created = await client.post("/api/experiments", headers=headers, json={
            "flag_id": str(flag_id), "name": "Resolve", "primary_metric": "click",
        })
        assert created.status_code == 201, f"Эксперимент не создан: {created.text}"
        experiment_id = created.json()["id"]
        path = f"/api/experiments/{experiment_id}"
        started = await client.patch(path, headers=headers, json={"status": "running"})
        assert started.status_code == 200, f"Эксперимент не запущен: {started.text}"
        first, second = await resolve(), await resolve()
        assert first.status_code == 200 and first.json() == second.json(), (
            f"Повтор запроса изменил назначение: {first.text}, {second.text}"
        )
        result = first.json()
        assert result["enabled"] and result["experiment_id"] == experiment_id, (
            f"Не возвращён запущенный эксперимент: {result}"
        )
        assert result["variant"] == result["variant_key"] in ("A", "B"), (
            f"Старое и новое поля варианта не согласованы: {result}"
        )
        await client.patch(path, headers=headers, json={"status": "paused"})
        paused = await resolve()
        assert not paused.json()["enabled"], "Приостановленный эксперимент назначил вариант"
        missing = await resolve("missing")
        assert missing.status_code == 404, f"Неизвестный флаг дал {missing.status_code}"


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
                yield flag.id, owner.id, other.id
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_experiment_lifecycle(experiment_db: tuple[UUID, UUID, UUID]) -> None:
    """API проверяет владельца, уникальность имени и переходы статусов."""
    flag_id, owner_id, other_id = experiment_db
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


@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка локальной MongoDB",
)
@pytest.mark.asyncio
async def test_events_require_valid_experiment_links(
    experiment_db: tuple[UUID, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Некорректные связи отклоняются до записи и публикации задачи."""
    flag_id, owner_id, _ = experiment_db
    headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    publish = Mock()
    monkeypatch.setattr(event_service.celery_app, "send_task", publish)

    with MongoClient(
        host=settings.MONGO_HOST, port=settings.MONGO_PORT, serverSelectionTimeoutMS=3000
    ) as mongo:
        database = mongo[f"codex_event_links_{uuid4().hex}"]
        collection = database["events"]
        monkeypatch.setitem(app.dependency_overrides, get_mongo_database, lambda: database)
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                variant = await client.post(
                    "/api/flags/checkout/variants", headers=headers,
                    json={"flag_id": str(flag_id), "variant_key": "A", "weight": 1},
                )
                assert variant.status_code == 201, f"Вариант не создан: {variant.text}"
                other_flag = await client.post(
                    "/api/flags", headers=headers, json={"key": "other", "type": "ab"},
                )
                assert other_flag.status_code == 201, (
                    f"Второй флаг не создан: {other_flag.text}"
                )
                foreign_variant = await client.post(
                    "/api/flags/other/variants", headers=headers,
                    json={"flag_id": other_flag.json()["id"], "variant_key": "B", "weight": 1},
                )
                assert foreign_variant.status_code == 201, (
                    f"Вариант второго флага не создан: {foreign_variant.text}"
                )
                experiment = await client.post(
                    "/api/experiments", headers=headers,
                    json={"flag_id": str(flag_id), "name": "Checkout", "primary_metric": "click"},
                )
                assert experiment.status_code == 201, f"Эксперимент не создан: {experiment.text}"
                experiment_id = experiment.json()["id"]
                path = f"/api/experiments/{experiment_id}"
                event = {
                    "experiment_id": experiment_id, "flag_key": "checkout",
                    "variant_key": "A", "event_type": "click", "user_id": "user-1",
                }
                draft = await client.post("/api/events/single", json=event)
                assert draft.status_code == 422, f"Событие черновика принято: {draft.text}"
                running = await client.patch(path, headers=headers, json={"status": "running"})
                assert running.status_code == 200, f"Эксперимент не запущен: {running.text}"

                for changes in (
                    {"experiment_id": str(uuid4())}, {"flag_key": "wrong"},
                    {"variant_key": "B"}, {"variant_key": None},
                ):
                    response = await client.post("/api/events/single", json={**event, **changes})
                    assert response.status_code == 422, (
                        f"Неверная связь {changes} не отклонена: {response.text}"
                    )
                mixed = await client.post(
                    "/api/events", json={"events": [event, {**event, "variant_key": "B"}]},
                )
                assert mixed.status_code == 422 and "Событие 2" in mixed.json()["detail"], (
                    f"Пакет должен указать второе событие: {mixed.text}"
                )
                count = collection.count_documents({})
                assert count == 0, f"Невалидные запросы сохранили {count} документов"
                assert publish.call_count == 0, "Невалидные запросы отправили задачу"

                single = await client.post("/api/events/single", json=event)
                batch = await client.post("/api/events", json={"events": [event, event]})
                assert single.status_code == 201, f"Одиночное событие отклонено: {single.text}"
                assert batch.status_code == 201, f"Корректный пакет отклонён: {batch.text}"
                paused = await client.patch(path, headers=headers, json={"status": "paused"})
                assert paused.status_code == 200, f"Не удалось приостановить: {paused.text}"
                rejected = await client.post("/api/events/single", json=event)
                assert rejected.status_code == 422, (
                    f"Приостановленный эксперимент принял событие: {rejected.text}"
                )
                count = collection.count_documents({"experiment_id": experiment_id})
                assert count == 3, f"Ожидалось три связанных события, получено {count}"
                assert publish.call_count == 2, (
                    f"Ожидались две задачи, отправлено {publish.call_count}"
                )
        finally:
            collection.drop()


@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка MongoDB",
)
@pytest.mark.asyncio
async def test_statistics_results_api(
    experiment_db: tuple[UUID, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Результаты сохраняются без дубликатов и доступны только владельцу."""
    flag_id, owner_id, other_id = experiment_db
    headers = {"Authorization": f"Bearer {create_access_token(str(owner_id))}"}
    foreign = {"Authorization": f"Bearer {create_access_token(str(other_id))}"}
    with MongoClient(
        host=settings.MONGO_HOST, port=settings.MONGO_PORT, serverSelectionTimeoutMS=3000
    ) as mongo:
        database = mongo[f"codex_statistics_{uuid4().hex}"]
        monkeypatch.setitem(app.dependency_overrides, get_mongo_database, lambda: database)
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
                headers=headers,
            ) as client:
                for key in ("A", "B"):
                    response = await client.post(
                        "/api/flags/checkout/variants",
                        json={"flag_id": str(flag_id), "variant_key": key},
                    )
                    assert response.status_code == 201, f"Не создан вариант: {response.text}"
                response = await client.post("/api/experiments", json={
                    "flag_id": str(flag_id), "name": "Statistics", "primary_metric": "buy",
                })
                assert response.status_code == 201, f"Не создан эксперимент: {response.text}"
                experiment_id = response.json()["id"]
                path = f"/api/experiments/{experiment_id}"
                response = await client.patch(path, json={"status": "running"})
                assert response.status_code == 200, f"Не запущен эксперимент: {response.text}"
                empty = await client.post(path + "/results")
                assert empty.status_code == 200, f"Ошибка пустого расчёта: {empty.text}"
                assert all(not row["enough_data"] for row in empty.json()), (
                    "Пустая выборка ошибочно отмечена как достаточная"
                )
                from datetime import datetime, timedelta, timezone
                start = datetime.now(timezone.utc)
                for key, successes in (("A", 10), ("B", 20)):
                    for i in range(100):
                        data = {
                            "experiment_id": experiment_id, "flag_key": "checkout",
                            "variant_key": key, "user_id": f"{key}-{i}",
                            "event_type": "impression", "timestamp": start,
                        }
                        database.events.insert_one(dict(data))
                        if i < successes:
                            database.events.insert_one({
                                **data, "event_type": "conversion", "event_name": "buy",
                                "timestamp": start + timedelta(seconds=1),
                            })
                for _ in range(2):
                    result = await client.post(path + "/results")
                    assert result.status_code == 200, f"Расчёт не выполнен: {result.text}"
                    rows = result.json()
                    assert [r["conversions"] for r in rows] == [10, 20], (
                        f"Ожидались 10 и 20 конверсий, получено {rows}"
                    )
                saved = await client.get(path + "/results")
                assert saved.status_code == 200 and len(saved.json()) == 2, (
                    f"Ожидался один снимок из двух строк: {saved.text}"
                )
                denied = await client.get(path + "/results", headers=foreign)
                assert denied.status_code == 403, f"Чужие результаты доступны: {denied.text}"
                state = await client.get(path)
                assert state.json()["status"] == "running", (
                    "Статистика самопроизвольно завершила эксперимент"
                )
                assert celery_app.conf.beat_schedule["calculate-statistics"]["schedule"] == 1800, (
                    "Статистика должна запускаться каждые 30 минут"
                )
        finally:
            database.events.drop()
