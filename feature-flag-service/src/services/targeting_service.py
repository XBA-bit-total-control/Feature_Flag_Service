from math import isfinite
from operator import ge, gt, le, lt
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.flag_targeting import FlagTargeting
from src.schemas.flag_targeting import FlagTargetingWrite


NUMERIC_COMPARISONS = {"gt": gt, "lt": lt, "gte": ge, "lte": le}


async def list_rules(
    db: AsyncSession, flag_id: UUID, *, enabled_only: bool = False,
) -> list[FlagTargeting]:
    """Получить правила флага, при необходимости только активные."""
    query = select(FlagTargeting).where(FlagTargeting.flag_id == flag_id)
    if enabled_only:
        query = query.where(FlagTargeting.is_enabled.is_(True))
    return list(await db.scalars(query.order_by(FlagTargeting.created_at, FlagTargeting.id)))


async def get_rule(db: AsyncSession, flag_id: UUID, rule_id: UUID) -> FlagTargeting | None:
    """Найти и заблокировать правило указанного флага для изменения."""
    return await db.scalar(select(FlagTargeting).where(
        FlagTargeting.flag_id == flag_id, FlagTargeting.id == rule_id,
    ).with_for_update())


async def create_rule(db: AsyncSession, flag_id: UUID, data: FlagTargetingWrite) -> FlagTargeting:
    """Создать правило для флага."""
    rule = FlagTargeting(flag_id=flag_id, **data.model_dump())
    db.add(rule)
    await db.flush()
    await db.refresh(rule)
    return rule


async def replace_rule(db: AsyncSession, rule: FlagTargeting, data: FlagTargetingWrite) -> FlagTargeting:
    """Заменить настройки существующего правила."""
    for field, value in data.model_dump().items():
        setattr(rule, field, value)
    await db.flush()
    await db.refresh(rule)
    return rule


async def delete_rule(db: AsyncSession, rule: FlagTargeting) -> None:
    """Удалить правило флага."""
    await db.delete(rule)
    await db.flush()


def matches_rule(rule: FlagTargeting, attributes: dict[str, Any]) -> bool:
    """Проверить активное правило по атрибутам без приведения типов."""
    try:
        config = FlagTargetingWrite.model_validate(rule)
    except ValidationError:
        return False

    actual = attributes.get(config.attribute)
    if type(actual) not in (str, bool, int, float):
        return False
    if isinstance(actual, float) and not isfinite(actual):
        return False

    compare = NUMERIC_COMPARISONS.get(config.operator)
    if compare is not None:
        return type(actual) in (int, float) and compare(actual, config.values[0])

    found = any(
        isinstance(actual, bool) == isinstance(value, bool) and actual == value
        for value in config.values
    )
    return not found if config.operator in ("ne", "not_in") else found
