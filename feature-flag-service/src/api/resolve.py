from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_db
from src.schemas.resolve import ResolveRequest, ResolveResponse
from src.services.resolve_service import (
    ResolveConfigurationError, ResolveNotFoundError, resolve_flag as resolve_service,
)

router = APIRouter()


@router.post("/resolve", response_model=ResolveResponse)
async def resolve_flag(
    data: ResolveRequest, db: AsyncSession = Depends(get_db)
) -> ResolveResponse:
    """Определить доступность флага и вариант для пользователя."""
    try:
        return await resolve_service(db, data)
    except ResolveNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ResolveConfigurationError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
