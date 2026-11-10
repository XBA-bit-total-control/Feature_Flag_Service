from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_async_session
from src.dependencies import get_current_user, require_writer
from src.models.flag import Flag
from src.models.flag_targeting import FlagTargeting
from src.models.user import User, UserRole
from src.schemas.flag import FlagCreate, FlagResponse, FlagUpdate
from src.schemas.flag_targeting import FlagTargetingResponse, FlagTargetingWrite
from src.schemas.flag_variant import (
    FlagVariantCreate,
    FlagVariantResponse,
    FlagVariantUpdate,
)
from src.services import flag_service, targeting_service
from src.services.audit_service import record_change, snapshot


router = APIRouter()


def _check_owner(flag: Flag, user: User) -> None:
    if flag.owner_id != user.id and user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Доступ разрешён владельцу флага или администратору")


@router.post("", response_model=FlagResponse, status_code=201)
async def create_flag(
    data: FlagCreate,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(require_writer),
) -> FlagResponse:
    """Создать новый feature-флаг."""
    existing = await flag_service.get_flag_by_key(db, data.key, include_deleted=True)
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="Флаг с таким ключом уже существует"
        )
    try:
        flag = await flag_service.create_flag(db, data, user.id)
    except flag_service.ConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    record_change(db, user.id, "flag", flag.id, "created", after=snapshot(flag))
    return flag


@router.get("", response_model=list[FlagResponse])
async def get_all_flags(
    flag_type: str | None = Query(default=None, alias="type", min_length=1, max_length=50),
    enabled: bool | None = None,
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=2 ** 31 - 1),
    db: AsyncSession = Depends(get_async_session),
) -> list[FlagResponse]:
    """Получить страницу флагов с фильтрами."""
    flags = await flag_service.get_all_flags(
        db, flag_type=flag_type, enabled=enabled, limit=limit, offset=offset,
    )
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
    user: User = Depends(require_writer),
) -> FlagResponse:
    """Обновить feature-флаг."""
    flag = await flag_service.get_flag_by_key(db, key, lock=True)
    if flag is None:
        raise HTTPException(status_code=404, detail="Флаг не найден")
    _check_owner(flag, user)
    if not data.model_dump(exclude_unset=True):
        raise HTTPException(
            status_code=400, detail="Тело запроса не может быть пустым"
        )
    before = snapshot(flag)
    try:
        flag = await flag_service.update_flag(db, flag, data)
    except flag_service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    record_change(db, user.id, "flag", flag.id, "updated", before=before, after=snapshot(flag))
    return flag


@router.delete("/{key}", status_code=204)
async def delete_flag(
    key: str,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(require_writer),
) -> None:
    """Удалить feature-флаг."""
    flag = await flag_service.get_flag_by_key(db, key, lock=True)
    if flag is None:
        raise HTTPException(status_code=404, detail="Флаг не найден")
    _check_owner(flag, user)
    before = snapshot(flag)
    try:
        await flag_service.delete_flag(db, flag)
    except flag_service.ConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    record_change(db, user.id, "flag", flag.id, "deleted", before=before, after=snapshot(flag))


@router.post(
    "/{key}/variants",
    response_model=FlagVariantResponse,
    status_code=201,
)
async def create_variant(
    key: str,
    data: FlagVariantCreate,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(require_writer),
) -> FlagVariantResponse:
    """Создать вариант A/B теста."""
    flag = await flag_service.get_flag_by_key(db, key, lock=True)
    if flag is None:
        raise HTTPException(status_code=404, detail="Флаг не найден")
    _check_owner(flag, user)
    variant_data = data.model_dump()
    variant_data["flag_id"] = flag.id
    try:
        variant = await flag_service.create_variant(
            db,
            FlagVariantCreate(**variant_data),
        )
    except flag_service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    record_change(db, user.id, "variant", variant.id, "created", after=snapshot(variant))
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


async def _get_owned_targeting_flag(
    key: str,
    request: Request,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> Flag:
    flag = await flag_service.get_flag_by_key(db, key, lock=request.method != "GET")
    if flag is None:
        raise HTTPException(status_code=404, detail="Флаг не найден")
    _check_owner(flag, user)
    return flag


async def _get_targeting_rule(
    rule_id: UUID,
    flag: Flag = Depends(_get_owned_targeting_flag),
    db: AsyncSession = Depends(get_async_session),
) -> FlagTargeting:
    rule = await targeting_service.get_rule(db, flag.id, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Правило таргетинга не найдено")
    return rule


@router.get("/{key}/targeting", response_model=list[FlagTargetingResponse])
async def get_targeting_rules(
    flag: Flag = Depends(_get_owned_targeting_flag),
    db: AsyncSession = Depends(get_async_session),
) -> list[FlagTargeting]:
    """Получить правила таргетинга флага."""
    return await targeting_service.list_rules(db, flag.id)


@router.post("/{key}/targeting", response_model=FlagTargetingResponse, status_code=201)
async def create_targeting_rule(
    data: FlagTargetingWrite,
    user: User = Depends(require_writer),
    flag: Flag = Depends(_get_owned_targeting_flag),
    db: AsyncSession = Depends(get_async_session),
) -> FlagTargeting:
    """Добавить правило таргетинга флага."""
    rule = await targeting_service.create_rule(db, flag.id, data)
    record_change(db, user.id, "targeting", rule.id, "created", after=snapshot(rule))
    return rule


@router.put("/{key}/targeting/{rule_id}", response_model=FlagTargetingResponse)
async def replace_targeting_rule(
    data: FlagTargetingWrite,
    user: User = Depends(require_writer),
    rule: FlagTargeting = Depends(_get_targeting_rule),
    db: AsyncSession = Depends(get_async_session),
) -> FlagTargeting:
    """Заменить правило таргетинга флага."""
    before = snapshot(rule)
    rule = await targeting_service.replace_rule(db, rule, data)
    record_change(db, user.id, "targeting", rule.id, "updated", before=before, after=snapshot(rule))
    return rule


@router.delete("/{key}/targeting/{rule_id}", status_code=204)
async def delete_targeting_rule(
    user: User = Depends(require_writer),
    rule: FlagTargeting = Depends(_get_targeting_rule),
    db: AsyncSession = Depends(get_async_session),
) -> None:
    """Удалить правило таргетинга флага."""
    record_change(db, user.id, "targeting", rule.id, "deleted", before=snapshot(rule))
    await targeting_service.delete_rule(db, rule)


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
    user: User = Depends(require_writer),
) -> FlagVariantResponse:
    """Обновить вариант A/B теста."""
    variant = await flag_service.get_variant_by_id(db, variant_id, lock=True)
    if variant is None:
        raise HTTPException(status_code=404, detail="Вариант не найден")
    _check_owner(variant.flag, user)
    before = snapshot(variant)
    try:
        variant = await flag_service.update_variant(db, variant, data)
    except flag_service.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    record_change(db, user.id, "variant", variant.id, "updated", before=before, after=snapshot(variant))
    return variant


@router.delete("/variants/{variant_id}", status_code=204)
async def delete_variant(
    variant_id: UUID,
    db: AsyncSession = Depends(get_async_session),
    user: User = Depends(require_writer),
) -> None:
    """Удалить вариант A/B теста."""
    variant = await flag_service.get_variant_by_id(db, variant_id, lock=True)
    if variant is None:
        raise HTTPException(status_code=404, detail="Вариант не найден")
    _check_owner(variant.flag, user)
    before = snapshot(variant)
    try:
        await flag_service.delete_variant(db, variant)
    except flag_service.ConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    record_change(db, user.id, "variant", variant.id, "deleted", before=before)
