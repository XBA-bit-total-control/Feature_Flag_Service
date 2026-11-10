from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pymongo.database import Database
from pymongo.errors import PyMongoError
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_mongo_database
from src.dependencies import get_db
from src.schemas.event import EventCreate
from src.schemas.resolve import ResolveRequest, ResolveResponse
from src.services.event_service import save_event, schedule_processing
from src.services.resolve_service import (
    ResolveConfigurationError, ResolveNotFoundError, resolve_flag as resolve_service,
)


router = APIRouter()


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_flag(
    data: ResolveRequest,
    db: AsyncSession = Depends(get_db),
    mongo: Database[dict[str, Any]] = Depends(get_mongo_database),
) -> ResolveResponse:
    """Определить доступность флага и сохранить показ назначенного A/B-варианта."""
    try:
        response = await resolve_service(db, data)
    except ResolveNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ResolveConfigurationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    if response.enabled and response.experiment_id is not None:
        impression = EventCreate(
            event_type="impression", flag_key=response.flag_key, user_id=response.user_id,
            experiment_id=response.experiment_id, variant_key=response.variant,
            metadata=data.attributes,
        )
        try:
            await run_in_threadpool(save_event, mongo, impression)
        except PyMongoError as error:
            raise HTTPException(
                status_code=503, detail="Не удалось сохранить показ варианта"
            ) from error
        await run_in_threadpool(schedule_processing)
    return response
