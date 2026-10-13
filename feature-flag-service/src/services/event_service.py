from datetime import datetime
from typing import Any

from pymongo import MongoClient

from src.schemas.event import (
    EventBatchResponse,
    EventCreate,
    EventResponse,
)


def _prepare_event_document(event: EventCreate) -> dict[str, Any]:
    """Подготовить документ события для MongoDB."""
    document = event.model_dump()
    document["timestamp"] = datetime.utcnow()
    document["processed"] = False
    document["processed_at"] = None
    document["created_at"] = document["timestamp"]
    return document


def save_event(
        mongo_client: MongoClient,
        event: EventCreate,
        collection_name: str = "events",
) -> EventResponse:
    """Сохранить одно событие в MongoDB."""
    collection = mongo_client[collection_name]
    document = _prepare_event_document(event)
    result = collection.insert_one(document)
    document["_id"] = str(result.inserted_id)
    return EventResponse(**document)


def save_events_batch(
        mongo_client: MongoClient,
        events: list[EventCreate],
        collection_name: str = "events",
) -> EventBatchResponse:
    """Сохранить пакет событий в MongoDB."""
    collection = mongo_client[collection_name]
    documents = [_prepare_event_document(event) for event in events]
    if not documents:
        return EventBatchResponse(accepted=0, rejected=len(events))
    result = collection.insert_many(documents, ordered=False)
    accepted = len(result.inserted_ids)
    rejected = len(events) - accepted
    return EventBatchResponse(accepted=accepted, rejected=rejected)
