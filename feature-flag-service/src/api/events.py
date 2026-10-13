from fastapi import APIRouter, Depends, HTTPException

from pymongo import MongoClient

from src.database import get_mongo_client
from src.schemas.event import (
    EventBatchCreate,
    EventBatchResponse,
    EventCreate,
    EventResponse,
)
from src.services.event_service import save_event, save_events_batch

router = APIRouter()


@router.post("/", response_model=EventBatchResponse, status_code=201)
async def create_events(
        data: EventBatchCreate,
        mongo: MongoClient = Depends(get_mongo_client),
) -> EventBatchResponse:
    """Пакетная отправка событий в MongoDB."""
    response = save_events_batch(mongo, data.events)
    return response


@router.post(
    "/single",
    response_model=EventResponse,
    status_code=201,
)
async def create_single_event(
        data: EventCreate,
        mongo: MongoClient = Depends(get_mongo_client),
) -> EventResponse:
    """Отправка одиночного события в MongoDB."""
    try:
        response = save_event(mongo, data)
        return response
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось сохранить событие: {str(e)}",
        )
