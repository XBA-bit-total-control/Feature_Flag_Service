import os
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from bson import ObjectId
from kombu.exceptions import OperationalError
from pymongo import MongoClient
from pymongo.errors import BulkWriteError, ServerSelectionTimeoutError

from src.api import events
from src.config import settings
from src.database import get_mongo_database
from src.event_indexes import ensure_event_indexes
from src.main import app
from src.services import event_service
from src.tasks import event_tasks


EVENT = {"event_type": "click", "flag_key": "checkout", "user_id": "user-1"}


@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка локальной MongoDB",
)
@pytest.mark.asyncio
async def test_events_api_writes_documents_and_creates_indexes(monkeypatch) -> None:
    """Маршруты сохраняют события, а повторный запуск сохраняет состав индексов."""
    with MongoClient(
        host=settings.MONGO_HOST,
        port=settings.MONGO_PORT,
        serverSelectionTimeoutMS=3000,
    ) as mongo:
        database = mongo[f"codex_test_events_{uuid4().hex}"]
        collection = database["events"]
        monkeypatch.setitem(
            app.dependency_overrides, get_mongo_database, lambda: database
        )
        publish = Mock(side_effect=[None, OperationalError("broker unavailable")])
        monkeypatch.setattr(event_service.celery_app, "send_task", publish)
        monkeypatch.setattr(event_tasks, "get_mongo_database", lambda: database)
        try:
            ensure_event_indexes(database)
            ensure_event_indexes(database)
            actual = {
                tuple(index["key"])
                for name, index in collection.index_information().items()
                if name != "_id_"
            }
            expected = {
                (("experiment_id", 1), ("variant_key", 1), ("event_type", 1)),
                (("experiment_id", 1), ("timestamp", 1)),
                (("processed", 1),),
                (("user_id", 1), ("experiment_id", 1)),
            }
            assert actual == expected, (
                f"Ожидались индексы {expected!r}, получены {actual!r}"
            )

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                single = await client.post("/api/events/single", json=EVENT)
                batch = await client.post(
                    "/api/events", json={"events": [EVENT, EVENT]}
                )

            assert single.status_code == 201, (
                f"Одиночная запись должна вернуть 201, получено {single.status_code}"
            )
            saved = collection.find_one({"_id": ObjectId(single.json()["id"])})
            assert saved is not None and saved.get("user_id") == EVENT["user_id"], (
                f"По id из ответа ожидался документ пользователя {EVENT['user_id']!r}, "
                f"получено {saved!r}"
            )
            assert batch.status_code == 201, (
                f"Пакетная запись должна вернуть 201, получено {batch.status_code}"
            )
            assert batch.json() == {"accepted": 2, "rejected": 0}, (
                f"Ожидались два принятых события без отказов, получено {batch.json()!r}"
            )
            count = collection.count_documents({"user_id": EVENT["user_id"]})
            assert count == 3, f"Ожидались три документа пользователя, получено {count}"
            assert publish.call_count == 2, (
                f"Ожидались две попытки публикации, получено {publish.call_count}"
            )
            processed = event_tasks.process_pending_events.run()
            assert processed == 3, (
                f"Ожидалась обработка трёх сохранённых событий, получено {processed}"
            )
            timestamps = {
                event["_id"]: event["processed_at"]
                for event in collection.find({"processed": True})
            }
            assert len(timestamps) == 3 and all(timestamps.values()), (
                "Все три обработанных события должны получить время обработки"
            )
            repeated = event_tasks.process_pending_events.run()
            assert repeated == 0, (
                f"Повторная обработка должна обновить ноль событий, получено {repeated}"
            )
            assert timestamps == {
                event["_id"]: event["processed_at"]
                for event in collection.find({"processed": True})
            }, "Повтор задачи изменил время обработки уже учтённых событий"
        finally:
            collection.drop()


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    {"events": []},
    {"events": None},
    {"events": [None]},
    {"events": [{**EVENT, "user_id": 123}]},
    {"events": [EVENT] * 101},
])
async def test_events_api_rejects_invalid_batch(body: dict, monkeypatch) -> None:
    """Некорректный пакет отклоняется до записи."""

    def unexpected_write(*args, **kwargs):
        pytest.fail("Сервис записи вызван для некорректного пакета")

    monkeypatch.setattr(events, "save_events_batch", unexpected_write)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/events", json=body)
    assert response.status_code == 422, (
        f"Некорректный пакет должен вернуть 422, получено {response.status_code}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("error", "expected_status"), [
    (ServerSelectionTimeoutError("private-mongo-host"), 503),
    (BulkWriteError({"nInserted": 1, "internal": "private-mongo-host"}), 500),
])
async def test_events_api_handles_write_failure(
    error, expected_status: int, monkeypatch
) -> None:
    """Сбой записи возвращает код ошибки без внутренних данных MongoDB."""

    def fail_write(*args, **kwargs):
        raise error

    monkeypatch.setattr(events, "save_events_batch", fail_write)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/events", json={"events": [EVENT, EVENT]})
    assert response.status_code == expected_status, (
        f"При сбое записи ожидался статус {expected_status}, "
        f"получено {response.status_code}"
    )
    assert "private-mongo-host" not in response.text, (
        "HTTP-ответ раскрыл внутренние данные исключения MongoDB"
    )
