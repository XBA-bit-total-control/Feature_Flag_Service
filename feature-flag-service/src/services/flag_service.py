from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.flag import Flag
from src.models.flag_variant import FlagVariant
from src.schemas.flag import FlagCreate, FlagUpdate
from src.schemas.flag_variant import FlagVariantCreate, FlagVariantUpdate


class ConflictError(ValueError):
    """Операция конфликтует с существующими данными."""


async def create_flag(
        db: AsyncSession,
        data: FlagCreate, user_id: UUID
) -> Flag:
    """Создать новый feature-флаг."""
    flag = Flag(**data.model_dump(), owner_id=user_id)
    db.add(flag)
    await db.flush()
    await db.refresh(flag)
    return flag


async def get_flag_by_key(
        db: AsyncSession,
        key: str
) -> Flag | None:
    """Получить флаг по уникальному ключу."""
    result = await db.execute(select(Flag).where(Flag.key == key))
    return result.scalar_one_or_none()


async def get_all_flags(db: AsyncSession) -> list[Flag]:
    """Получить список всех флагов."""
    result = await db.execute(select(Flag))
    return list(result.scalars().all())


async def update_flag(
        db: AsyncSession,
        flag: Flag,
        data: FlagUpdate
) -> Flag:
    """Обновить feature-флаг."""
    update_data = data.model_dump(exclude_unset=True)
    if "key" in update_data and update_data["key"] is not None:
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
    await db.flush()
    await db.refresh(flag)
    return flag


async def delete_flag(db: AsyncSession, flag: Flag) -> None:
    """Удалить feature-флаг."""
    await db.delete(flag)
    await db.flush()


async def create_variant(
        db: AsyncSession,
        data: FlagVariantCreate
) -> FlagVariant:
    """Создать вариант A/B теста."""
    await ensure_variant_key_available(db, data.flag_id, data.variant_key)
    variant = FlagVariant(**data.model_dump())
    db.add(variant)
    await db.flush()
    await db.refresh(variant)
    return variant


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
        db: AsyncSession, variant_id: UUID
) -> FlagVariant | None:
    """Получить вариант по ID."""
    result = await db.execute(
        select(FlagVariant)
        .options(selectinload(FlagVariant.flag))
        .where(FlagVariant.id == variant_id)
    )
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
    if update_data.get("variant_key") is not None:
        await ensure_variant_key_available(
            db, variant.flag_id, update_data["variant_key"], variant.id
        )
    for field, value in update_data.items():
        if value is None and field != "description":
            raise ValueError(f"Поле '{field}' не может быть null")
        setattr(variant, field, value)
    await db.flush()
    await db.refresh(variant)
    return variant


async def delete_variant(
        db: AsyncSession,
        variant: FlagVariant
) -> None:
    """Удалить вариант A/B теста."""
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
