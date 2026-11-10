from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.experiment import Experiment
from src.models.flag import Flag
from src.models.flag_variant import FlagVariant
from src.schemas.flag import FlagCreate, FlagUpdate
from src.schemas.flag_variant import FlagVariantCreate, FlagVariantUpdate
from src.services.experiment_service import ACTIVE_STATUSES


class ConflictError(ValueError):
    """Операция конфликтует с существующими данными."""


async def create_flag(
    db: AsyncSession,
    data: FlagCreate, user_id: UUID
) -> Flag:
    """Создать новый feature-флаг."""
    flag = Flag(**data.model_dump(), owner_id=user_id)
    db.add(flag)
    return await _flush_flag(db, flag)


async def get_flag_by_key(
    db: AsyncSession,
    key: str, *, include_deleted: bool = False, lock: bool = False,
) -> Flag | None:
    """Получить флаг по уникальному ключу."""
    query = select(Flag).where(Flag.key == key)
    if not include_deleted:
        query = query.where(Flag.deleted_at.is_(None))
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_all_flags(
    db: AsyncSession, *, flag_type: str | None, enabled: bool | None, limit: int, offset: int,
) -> list[Flag]:
    """Получить страницу флагов с фильтрами по типу и доступности."""
    query = select(Flag).where(Flag.deleted_at.is_(None))
    if flag_type is not None:
        query = query.where(Flag.type == flag_type)
    if enabled is not None:
        query = query.where(Flag.enabled == enabled)
    return list(await db.scalars(query.order_by(Flag.key).offset(offset).limit(limit)))


async def update_flag(
    db: AsyncSession,
    flag: Flag,
    data: FlagUpdate
) -> Flag:
    """Обновить feature-флаг."""
    update_data = data.model_dump(exclude_unset=True)
    if "key" in update_data and update_data["key"] is not None:
        if update_data["key"] != flag.key:
            await ensure_no_active_experiment(db, flag.id)
        existing = await db.execute(
            select(Flag).where(Flag.key == update_data["key"])
        )
        existing_flag = existing.scalar_one_or_none()
        if existing_flag is not None and existing_flag.id != flag.id:
            raise ConflictError("Флаг с таким ключом уже существует")
    for field, value in update_data.items():
        if value is None and field != "description":
            raise ValueError(f"Поле '{field}' не может быть null")
        setattr(flag, field, value)
    return await _flush_flag(db, flag)


async def _flush_flag(db: AsyncSession, flag: Flag) -> Flag:
    try:
        await db.flush()
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise ConflictError("Флаг с таким ключом уже существует") from error
        raise
    await db.refresh(flag)
    return flag


async def delete_flag(db: AsyncSession, flag: Flag) -> None:
    """Скрыть флаг, сохранив историю связанных экспериментов."""
    await ensure_no_active_experiment(db, flag.id)
    flag.deleted_at = datetime.now(timezone.utc)
    flag.enabled = False
    await db.flush()


async def ensure_no_active_experiment(db: AsyncSession, flag_id: UUID) -> None:
    """Защитить запущенный или приостановленный эксперимент от изменения связей."""
    active_id = await db.scalar(select(Experiment.id).where(
        Experiment.flag_id == flag_id, Experiment.status.in_(ACTIVE_STATUSES),
    ).limit(1))
    if active_id is not None:
        raise ConflictError("У флага есть запущенный или приостановленный эксперимент")


async def create_variant(
    db: AsyncSession,
    data: FlagVariantCreate
) -> FlagVariant:
    """Создать вариант A/B теста."""
    await ensure_no_active_experiment(db, data.flag_id)
    await ensure_variant_key_available(db, data.flag_id, data.variant_key)
    variant = FlagVariant(**data.model_dump())
    db.add(variant)
    return await _flush_variant(db, variant)


async def get_variants(
    db: AsyncSession,
    flag_id: UUID
) -> list[FlagVariant]:
    """Получить список вариантов для флага."""
    result = await db.execute(
        select(FlagVariant)
        .where(FlagVariant.flag_id == flag_id)
        .order_by(FlagVariant.id)
    )
    return list(result.scalars().all())


async def get_variant_by_id(
    db: AsyncSession, variant_id: UUID, *, lock: bool = False,
) -> FlagVariant | None:
    """Получить вариант по ID."""
    query = select(FlagVariant).join(Flag).options(selectinload(FlagVariant.flag)).where(
        FlagVariant.id == variant_id, Flag.deleted_at.is_(None),
    )
    if lock:
        query = query.with_for_update(of=(Flag, FlagVariant)).execution_options(populate_existing=True)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def update_variant(
    db: AsyncSession,
    variant: FlagVariant,
    data: FlagVariantUpdate
) -> FlagVariant:
    """Обновить вариант A/B теста."""
    update_data = data.model_dump(exclude_unset=True)
    if not update_data:
        raise ValueError("Тело запроса не может быть пустым")
    if any(field in update_data and update_data[field] != getattr(variant, field)
           for field in ("variant_key", "weight")):
        await ensure_no_active_experiment(db, variant.flag_id)
    if update_data.get("variant_key") is not None:
        await ensure_variant_key_available(
            db, variant.flag_id, update_data["variant_key"], variant.id
        )
    for field, value in update_data.items():
        if value is None and field != "description":
            raise ValueError(f"Поле '{field}' не может быть null")
        setattr(variant, field, value)
    return await _flush_variant(db, variant)


async def _flush_variant(db: AsyncSession, variant: FlagVariant) -> FlagVariant:
    try:
        await db.flush()
    except IntegrityError as error:
        if getattr(error.orig.__cause__, "constraint_name", None) == "uq_flag_variants_flag_key":
            raise ConflictError("Вариант с таким ключом уже существует у флага") from error
        raise
    await db.refresh(variant)
    return variant


async def delete_variant(
    db: AsyncSession,
    variant: FlagVariant
) -> None:
    """Удалить вариант A/B теста."""
    await ensure_no_active_experiment(db, variant.flag_id)
    await db.delete(variant)
    await db.flush()


async def ensure_variant_key_available(
    db: AsyncSession,
    flag_id: UUID,
    variant_key: str,
    exclude_id: UUID | None = None,
) -> None:
    """Проверить доступность ключа варианта у флага, исключая указанный вариант."""
    query = select(FlagVariant.id).where(
        FlagVariant.flag_id == flag_id,
        FlagVariant.variant_key == variant_key,
    )
    if exclude_id is not None:
        query = query.where(FlagVariant.id != exclude_id)
    if (await db.execute(query.limit(1))).first() is not None:
        raise ConflictError("Вариант с таким ключом уже существует у флага")
