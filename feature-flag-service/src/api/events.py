from typing import Any

from bson.errors import InvalidDocument
from fastapi import APIRouter, Depends, HTTPException
from pymongo.database import Database
from pymongo.errors import ConnectionFailure, DocumentTooLarge, PyMongoError

from src.database import get_mongo_database
from src.schemas.event import (
    EventBatchCreate,
    EventBatchResponse,
    EventCreate,
    EventResponse,
)
from src.services.event_service import save_event, save_events_batch


router = APIRouter()


def _mongo_write_error(error: Exception) -> HTTPException:
    if isinstance(error, (InvalidDocument, DocumentTooLarge, OverflowError)):
        return HTTPException(
            status_code=422, detail="Данные события нельзя сохранить в MongoDB"
        )
    if isinstance(error, ConnectionFailure):
        return HTTPException(status_code=503, detail="Хранилище событий недоступно")
    return HTTPException(status_code=500, detail="Не удалось сохранить события")


@router.post("", response_model=EventBatchResponse, status_code=201)
def create_events(
        data: EventBatchCreate,
        mongo: Database[dict[str, Any]] = Depends(get_mongo_database),
) -> EventBatchResponse:
    """Сохранить пакет событий. При ошибке часть пакета может быть записана."""
    try:
        return save_events_batch(mongo, data.events)
    except (PyMongoError, InvalidDocument, OverflowError) as error:
        raise _mongo_write_error(error) from error


@router.post("/single", response_model=EventResponse, status_code=201)
def create_single_event(
        data: EventCreate,
        mongo: Database[dict[str, Any]] = Depends(get_mongo_database),
) -> EventResponse:
    """Сохранить одиночное событие в MongoDB."""
    try:
        return save_event(mongo, data)
    except (PyMongoError, InvalidDocument, OverflowError) as error:
        raise _mongo_write_error(error) from error
