from typing import Any

from bson.errors import InvalidDocument
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pymongo.database import Database
from pymongo.errors import ConnectionFailure, DocumentTooLarge, PyMongoError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_mongo_database
from src.dependencies import get_db
from src.schemas.event import (
    EventBatchCreate,
    EventBatchResponse,
    EventCreate,
    EventResponse,
)
from src.services.event_links import EventLinkError, validate_event_links
from src.services.event_service import save_event, save_events_batch, schedule_processing


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
async def create_events(
    data: EventBatchCreate,
    mongo: Database[dict[str, Any]] = Depends(get_mongo_database),
    db: AsyncSession = Depends(get_db),
) -> EventBatchResponse:
    """Сохранить пакет событий. При ошибке часть пакета может быть записана."""
    try:
        await validate_event_links(db, data.events)
        response = await run_in_threadpool(save_events_batch, mongo, data.events)
    except EventLinkError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (PyMongoError, InvalidDocument, OverflowError) as error:
        raise _mongo_write_error(error) from error
    await run_in_threadpool(schedule_processing)
    return response


@router.post("/single", response_model=EventResponse, status_code=201)
async def create_single_event(
    data: EventCreate,
    mongo: Database[dict[str, Any]] = Depends(get_mongo_database),
    db: AsyncSession = Depends(get_db),
) -> EventResponse:
    """Сохранить одиночное событие в MongoDB."""
    try:
        await validate_event_links(db, [data])
        response = await run_in_threadpool(save_event, mongo, data)
    except EventLinkError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (PyMongoError, InvalidDocument, OverflowError) as error:
        raise _mongo_write_error(error) from error
    await run_in_threadpool(schedule_processing)
    return response
