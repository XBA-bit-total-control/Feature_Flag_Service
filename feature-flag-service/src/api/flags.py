from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_async_session
from src.schemas.flag import FlagCreate, FlagResponse, FlagUpdate
from src.schemas.flag_variant import FlagVariantCreate, FlagVariantResponse
from src.services import flag_service

router = APIRouter()


@router.post("/", response_model=FlagResponse, status_code=201)
async def create_flag(
    data: FlagCreate,
    db: AsyncSession = Depends(get_async_session),
) -> FlagResponse:
    """Создать новый feature-флаг."""
    flag = await flag_service.create_flag(db, data)
    return flag


@router.get("/", response_model=list[FlagResponse])
async def get_all_flags(
    db: AsyncSession = Depends(get_async_session),
) -> list[FlagResponse]:
    """Получить список всех флагов."""
    flags = await flag_service.get_all_flags(db)
    return flags


@router.get("/{key}", response_model=FlagResponse)
async def get_flag_by_key(
    key: str,
    db: AsyncSession = Depends(get_async_session),
) -> FlagResponse:
    """Получить флаг по уникальному ключу."""
    flag = await flag_service.get_flag_by_key(db, key)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    return flag


@router.patch("/{key}", response_model=FlagResponse)
async def update_flag(
    key: str,
    data: FlagUpdate,
    db: AsyncSession = Depends(get_async_session),
) -> FlagResponse:
    """Обновить feature-флаг."""
    flag = await flag_service.get_flag_by_key(db, key)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    return await flag_service.update_flag(db, flag, data)


@router.delete("/{key}", status_code=204)
async def delete_flag(
    key: str,
    db: AsyncSession = Depends(get_async_session),
) -> None:
    """Удалить feature-флаг."""
    flag = await flag_service.get_flag_by_key(db, key)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    await flag_service.delete_flag(db, flag)
    return None


@router.post(
    "/{key}/variants",
    response_model=FlagVariantResponse,
    status_code=201,
)
async def create_variant(
    key: str,
    data: FlagVariantCreate,
    db: AsyncSession = Depends(get_async_session),
) -> FlagVariantResponse:
    """Создать вариант A/B теста."""
    flag = await flag_service.get_flag_by_key(db, key)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    variant_data = data.model_dump()
    variant_data["flag_id"] = flag.id
    variant = await flag_service.create_variant(
        db,
        FlagVariantCreate(**variant_data),
    )
    return variant


@router.get("/{key}/variants", response_model=list[FlagVariantResponse])
async def get_variants(
    key: str,
    db: AsyncSession = Depends(get_async_session),
) -> list[FlagVariantResponse]:
    """Получить список вариантов для флага."""
    flag = await flag_service.get_flag_by_key(db, key)
    if not flag:
        raise HTTPException(status_code=404, detail="Flag not found")
    variants = await flag_service.get_variants(db, flag.id)
    return variants
