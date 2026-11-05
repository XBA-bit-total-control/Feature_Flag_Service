from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from bson import ObjectId
from pydantic import ValidationError
from pymongo.errors import BulkWriteError

from src.schemas.event import EventBatchCreate, EventCreate
from src.services.event_service import (
    _prepare_event_document,
    save_event,
    save_events_batch,
)


def make_event(**overrides) -> EventCreate:
    """Создать тестовое событие с возможностью переопределить поля."""
    data = {
        "event_type": "conversion",
        "flag_key": "checkout",
        "user_id": "user-1",
        "experiment_id": uuid4(),
        "timestamp": "2026-01-01T12:00:00+03:00",
        "metadata": {"country": "RU", "nested": {"values": [1, None]}},
    }
    return EventCreate(**(data | overrides))


def test_document_preserves_event_time_and_serializes_uuid() -> None:
    """Документ сохраняет время события в UTC и содержит UUID в виде строки."""
    event = make_event()
    before = datetime.now(timezone.utc)
    document = _prepare_event_document(event)
    assert document["experiment_id"] == str(event.experiment_id), (
        f"Ожидался UUID в виде строки {str(event.experiment_id)!r}, "
        f"получено {document['experiment_id']!r}"
    )
    expected_timestamp = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)
    assert document["timestamp"] == expected_timestamp, (
        f"Время события должно быть {expected_timestamp}, "
        f"получено {document['timestamp']}"
    )
    after = datetime.now(timezone.utc)
    assert before <= document["created_at"] <= after, (
        f"Время создания должно быть между {before} и {after}, "
        f"получено {document['created_at']}"
    )
    assert document["metadata"] == event.metadata, (
        f"Ожидались метаданные {event.metadata!r}, получено {document['metadata']!r}"
    )
    assert document["processed"] is False, (
        f"Новое событие должно иметь processed=False, получено {document['processed']!r}"
    )
    assert document["processed_at"] is None, (
        f"До обработки processed_at должен быть None, получено {document['processed_at']!r}"
    )
    assert "_id" not in document, "Поле _id должно появляться только при вставке"


def test_naive_and_default_timestamps_are_utc() -> None:
    """Время без часового пояса и время по умолчанию считаются UTC."""
    naive_event = make_event(timestamp="2026-01-01T09:00:00")
    assert naive_event.timestamp.utcoffset() == timedelta(0), (
        f"Время без часового пояса должно считаться UTC, получено {naive_event.timestamp!r}"
    )
    event = EventCreate(event_type="click", flag_key="checkout", user_id="user-1")
    assert event.timestamp.utcoffset() == timedelta(0), (
        f"Время по умолчанию должно быть в UTC, получено {event.timestamp!r}"
    )
    experiment_id = _prepare_event_document(event)["experiment_id"]
    assert experiment_id is None, (
        f"При отсутствии эксперимента ожидался experiment_id=None, получено {experiment_id!r}"
    )


@pytest.mark.parametrize(
    "field", ["_id", "id", "processed", "processed_at", "created_at"]
)
def test_rejects_service_fields(field: str) -> None:
    """Событие со служебным полем не проходит валидацию."""
    with pytest.raises(ValidationError):
        make_event(**{field: "injected"})


@pytest.mark.parametrize("data", [
    {"user_id": ""}, {"user_id": None}, {"event_type": None},
    {"experiment_id": "not-uuid"}, {"timestamp": None},
    {"metadata": []}, {"value": -1}, {"value": float("inf")},
])
def test_rejects_invalid_event(data: dict) -> None:
    """Событие с недопустимым значением не проходит валидацию."""
    with pytest.raises(ValidationError):
        make_event(**data)


def test_single_event_response_has_inserted_id() -> None:
    """Ответ содержит строковый идентификатор вставленного документа."""
    database = MagicMock()
    event_id = ObjectId()
    database["events"].insert_one.return_value.inserted_id = event_id
    response = save_event(database, make_event())
    assert response.id == str(event_id), (
        f"В ответе ожидался идентификатор вставленного документа {event_id}, "
        f"получено {response.id!r}"
    )
    assert response.experiment_id is not None, (
        "В ответе отсутствует идентификатор эксперимента из события"
    )
    assert response.timestamp.hour == 9, (
        f"В ответе ожидалось время события 09:00 UTC, получено {response.timestamp!r}"
    )
    insert_count = database["events"].insert_one.call_count
    assert insert_count == 1, (
        f"Для одного события ожидался один вызов insert_one, получено {insert_count}"
    )


def test_empty_batch_does_not_access_database() -> None:
    """Сервис пропускает пустой пакет, а входная схема отклоняет его."""
    database = MagicMock()
    response = save_events_batch(database, [])
    assert response.accepted == response.rejected == 0, (
        f"Для пустого пакета ожидались нулевые счётчики, "
        f"получено принято {response.accepted}, отклонено {response.rejected}"
    )
    collection_accesses = database.__getitem__.call_count
    assert collection_accesses == 0, (
        f"Пустой пакет не должен обращаться к коллекции, получено {collection_accesses}"
    )
    with pytest.raises(ValidationError):
        EventBatchCreate(events=[])


def test_repeated_events_are_independent_documents() -> None:
    """Повторяющиеся события записываются отдельными документами."""
    database = MagicMock()
    database["events"].insert_many.return_value.inserted_ids = [ObjectId(), ObjectId()]
    event = make_event()
    response = save_events_batch(database, [event, event])
    documents = database["events"].insert_many.call_args.args[0]
    assert response.accepted == 2, (
        f"Ожидались два принятых события, получено {response.accepted}"
    )
    assert response.rejected == 0, (
        f"Ожидалось ноль отклонённых событий, получено {response.rejected}"
    )
    assert documents[0] is not documents[1], (
        "Для повторного события переиспользован словарь первого документа"
    )
    assert event.experiment_id is not None, (
        "После подготовки пакета исходное событие потеряло идентификатор эксперимента"
    )


def test_batch_write_error_is_not_reported_as_success() -> None:
    """Ошибка частичной записи пакета передаётся вызывающему коду."""
    database = MagicMock()
    database["events"].insert_many.side_effect = BulkWriteError({
        "writeErrors": [{"index": 1, "code": 11000, "errmsg": "duplicate"}],
        "writeConcernErrors": [], "nInserted": 1,
    })
    with pytest.raises(BulkWriteError):
        save_events_batch(database, [make_event(), make_event()])
