import logging
from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database
from kombu.exceptions import OperationalError
from src.celery_app import app as celery_app
from src.observability import log_event
from src.schemas.event import (
    EventBatchResponse,
    EventCreate,
    EventResponse,
)


def schedule_processing() -> None:
    """Поставить задачу после записи событий, если брокер доступен."""
    try:
        celery_app.send_task("events.process_pending", retry=False, ignore_result=True)
    except OperationalError:
        # Сохранённые события можно поставить в обработку повторно.
        log_event("events.publish_failed", level=logging.WARNING, task="events.process_pending")


def _prepare_event_document(event: EventCreate) -> dict[str, Any]:
    """Подготовить документ события для MongoDB."""
    document = event.model_dump(mode="json")
    document["timestamp"] = event.timestamp
    document["processed"] = False
    document["processed_at"] = None
    document["created_at"] = datetime.now(timezone.utc)
    return document


def save_event(
        mongo_database: Database[dict[str, Any]],
        event: EventCreate,
        collection_name: str = "events",
) -> EventResponse:
    """Сохранить одно событие в MongoDB."""
    collection = mongo_database[collection_name]
    document = _prepare_event_document(event)
    result = collection.insert_one(document)
    return EventResponse(id=str(result.inserted_id), **document)


def save_events_batch(
        mongo_database: Database[dict[str, Any]],
        events: list[EventCreate],
        collection_name: str = "events",
) -> EventBatchResponse:
    """Сохранить пакет событий в MongoDB."""
    if not events:
        return EventBatchResponse(accepted=0, rejected=0)
    collection = mongo_database[collection_name]
    documents = [_prepare_event_document(event) for event in events]
    result = collection.insert_many(documents, ordered=False)
    accepted = len(result.inserted_ids)
    rejected = len(events) - accepted
    return EventBatchResponse(accepted=accepted, rejected=rejected)
