from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession


from src.database import get_async_session
from src.dependencies import get_current_user
from src.models.user import User
from src.schemas.flag import FlagCreate, FlagResponse, FlagUpdate
from src.schemas.flag_variant import (
    FlagVariantCreate,
    FlagVariantResponse,
    FlagVariantUpdate,
)
from src.services import flag_service

router = APIRouter()


@router.post("", response_model=FlagResponse, status_code=201)
async def create_flag(
    data: FlagCreate,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> FlagResponse:
    """Создать новый feature-флаг."""
    existing = await flag_service.get_flag_by_key(db, data.key)
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="Флаг с таким ключом уже существует"
        )
    flag = await flag_service.create_flag(db, data, user.id)
    return flag


@router.get("", response_model=list[FlagResponse])
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
    if flag is None:
        raise HTTPException(status_code=404, detail="Флаг не найден")
    return flag


@router.patch("/{key}", response_model=FlagResponse)
async def update_flag(
    key: str,
    data: FlagUpdate,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> FlagResponse:
    """Обновить feature-флаг."""
    flag = await flag_service.get_flag_by_key(db, key)
    if flag is None:
        raise HTTPException(status_code=404, detail="Флаг не найден")
    if flag.owner_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Доступ запрещён: только владелец может обновлять флаг",
        )
    if not data.model_dump(exclude_unset=True):
        raise HTTPException(
            status_code=400, detail="Тело запроса не может быть пустым"
        )
    try:
        return await flag_service.update_flag(db, flag, data)
    except flag_service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{key}", status_code=204)
async def delete_flag(
    key: str,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> None:
    """Удалить feature-флаг."""
    flag = await flag_service.get_flag_by_key(db, key)
    if flag is None:
        raise HTTPException(status_code=404, detail="Флаг не найден")
    if flag.owner_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Доступ запрещён: только владелец может удалить флаг",
        )
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
    user: User = Depends(get_current_user),
) -> FlagVariantResponse:
    """Создать вариант A/B теста."""
    flag = await flag_service.get_flag_by_key(db, key)
    if flag is None:
        raise HTTPException(status_code=404, detail="Флаг не найден")
    if flag.owner_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Доступ запрещён: только владелец может создавать варианты",
        )
    variant_data = data.model_dump()
    variant_data["flag_id"] = flag.id
    try:
        variant = await flag_service.create_variant(
            db,
            FlagVariantCreate(**variant_data),
        )
    except flag_service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return variant


@router.get("/{key}/variants", response_model=list[FlagVariantResponse])
async def get_variants(
    key: str,
    db: AsyncSession = Depends(get_async_session),
) -> list[FlagVariantResponse]:
    """Получить список вариантов для флага."""
    flag = await flag_service.get_flag_by_key(db, key)
    if flag is None:
        raise HTTPException(status_code=404, detail="Флаг не найден")
    variants = await flag_service.get_variants(db, flag.id)
    return variants


@router.get("/variants/{variant_id}", response_model=FlagVariantResponse)
async def get_variant(
    variant_id: UUID,
    db: AsyncSession = Depends(get_async_session),
) -> FlagVariantResponse:
    """Получить вариант по ID."""
    variant = await flag_service.get_variant_by_id(db, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Вариант не найден")
    return variant


@router.patch(
    "/variants/{variant_id}",
    response_model=FlagVariantResponse,
)
async def update_variant(
    variant_id: UUID,
    data: FlagVariantUpdate,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> FlagVariantResponse:
    """Обновить вариант A/B теста."""
    variant = await flag_service.get_variant_by_id(db, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Вариант не найден")
    if variant.flag.owner_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Доступ запрещён: только владелец может обновлять варианты",
        )
    try:
        return await flag_service.update_variant(db, variant, data)
    except flag_service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/variants/{variant_id}", status_code=204)
async def delete_variant(
    variant_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> None:
    """Удалить вариант A/B теста."""
    variant = await flag_service.get_variant_by_id(db, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Вариант не найден")
    if variant.flag.owner_id != user.id:
        raise HTTPException(
            status_code=403,
            detail="Доступ запрещён: только владелец может удалять варианты",
        )
    await flag_service.delete_variant(db, variant)
    return None
