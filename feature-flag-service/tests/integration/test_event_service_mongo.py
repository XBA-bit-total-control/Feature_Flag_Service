"""Проверка реальной MongoDB: RUN_MONGO_INTEGRATION=1 python -m pytest ..."""

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from bson import ObjectId
from pymongo import MongoClient

from src.config import settings
from src.schemas.event import EventCreate
from src.services.event_service import save_event, save_events_batch


@pytest.mark.skipif(
    os.environ.get("RUN_MONGO_INTEGRATION") != "1",
    reason="Нужна явно включённая проверка локальной MongoDB",
)
def test_event_service_with_real_mongo() -> None:
    """Одиночное событие и пакет сохраняются в MongoDB с ожидаемыми полями."""
    collection_name = f"codex_test_events_{uuid4().hex}"
    with MongoClient(
        host=settings.MONGO_HOST,
        port=settings.MONGO_PORT,
        serverSelectionTimeoutMS=3000,
        tz_aware=True,
    ) as client:
        client.admin.command("ping")
        database = client[settings.MONGO_DB]
        collection = database[collection_name]
        try:
            experiment_id = uuid4()
            event = EventCreate(
                event_type="conversion", flag_key="mongo-check", user_id="user-1",
                experiment_id=experiment_id, timestamp="2026-01-01T12:00:00+03:00",
                metadata={"nested": {"values": [1, None, "RU"]}},
            )
            response = save_event(database, event, collection_name)
            saved = collection.find_one({"_id": ObjectId(response.id)})
            assert saved is not None, (
                f"В MongoDB не найден документ с возвращённым id={response.id}"
            )
            assert saved["experiment_id"] == str(experiment_id), (
                f"В MongoDB ожидался UUID в виде строки {str(experiment_id)!r}, "
                f"получено {saved['experiment_id']!r}"
            )
            expected_timestamp = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
            assert saved["timestamp"] == expected_timestamp, (
                f"В MongoDB ожидалось время события {expected_timestamp}, "
                f"получено {saved['timestamp']!r}"
            )
            assert saved["created_at"] > saved["timestamp"], (
                f"Время записи {saved['created_at']!r} должно быть позже "
                f"времени тестового события {saved['timestamp']!r}"
            )
            assert saved["metadata"] == event.metadata, (
                f"Ожидались метаданные {event.metadata!r}, получено {saved['metadata']!r}"
            )
            assert saved["processed"] is False, (
                f"Новое событие должно иметь processed=False, получено {saved['processed']!r}"
            )
            assert saved["processed_at"] is None, (
                f"До обработки ожидалось processed_at=None, получено {saved['processed_at']!r}"
            )
            batch = save_events_batch(database, [event, event], collection_name)
            assert batch.accepted == 2 and batch.rejected == 0, (
                f"Ожидались два принятых и ноль отклонённых событий, "
                f"получено принято {batch.accepted}, отклонено {batch.rejected}"
            )
            document_count = collection.count_documents({})
            assert document_count == 3, (
                f"После одиночной и пакетной записи ожидались три документа, "
                f"получено {document_count}"
            )
            empty_batch = save_events_batch(database, [], collection_name)
            assert empty_batch.accepted == 0, (
                f"Пустой пакет должен принять ноль событий, получено {empty_batch.accepted}"
            )
            count_after_empty_batch = collection.count_documents({})
            assert count_after_empty_batch == document_count, (
                f"Пустой пакет изменил число документов с {document_count} "
                f"до {count_after_empty_batch}"
            )
        finally:
            database.drop_collection(collection_name)
