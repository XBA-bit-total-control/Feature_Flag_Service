from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.flag import Flag
from src.models.flag_variant import FlagVariant
from src.schemas.flag import FlagCreate, FlagUpdate
from src.schemas.flag_variant import FlagVariantCreate


async def create_flag(db: AsyncSession, data: FlagCreate) -> Flag:
    """Создать новый feature-флаг."""
    flag = Flag(**data.model_dump())
    db.add(flag)
    await db.flush()
    await db.refresh(flag)
    return flag


async def get_flag_by_key(db: AsyncSession, key: str) -> Flag | None:
    """Получить флаг по уникальному ключу."""
    result = await db.execute(select(Flag).where(Flag.key == key))
    return result.scalar_one_or_none()


async def get_all_flags(db: AsyncSession) -> list[Flag]:
    """Получить список всех флагов."""
    result = await db.execute(select(Flag))
    return list(result.scalars().all())


async def update_flag(db: AsyncSession, flag: Flag, data: FlagUpdate) -> Flag:
    """Обновить feature-флаг."""
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(flag, field, value)
    await db.flush()
    await db.refresh(flag)
    return flag


async def delete_flag(db: AsyncSession, flag: Flag) -> None:
    """Удалить feature-флаг."""
    await db.delete(flag)
    await db.flush()


async def create_variant(db: AsyncSession, data: FlagVariantCreate) -> FlagVariant:
    """Создать вариант A/B теста."""
    variant = FlagVariant(**data.model_dump())
    db.add(variant)
    await db.flush()
    await db.refresh(variant)
    return variant


async def get_variants(db: AsyncSession, flag_id: UUID) -> list[FlagVariant]:
    """Получить список вариантов для флага."""
    result = await db.execute(select(FlagVariant).where(FlagVariant.flag_id == flag_id))
    return list(result.scalars().all())
