import json
from typing import Any
from uuid import UUID

from pydantic_core import to_jsonable_python
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.audit_log import AuditLog
from src.models.base import Base


def snapshot(entity: Base) -> dict[str, Any]:
    """Снять состояние конфигурации флага, варианта, правила или эксперимента."""
    return {
        column.key: to_jsonable_python(getattr(entity, column.key))
        for column in inspect(entity).mapper.column_attrs
        if column.key not in {"created_at", "updated_at"}
    }


def record_change(
    db: AsyncSession, actor_id: UUID, entity_type: str, entity_id: UUID, action: str,
    *, before: dict[str, Any] | None = None, after: dict[str, Any] | None = None,
) -> None:
    """Записать изменение в той же транзакции, что и основную операцию."""
    if before == after:
        return
    db.add(AuditLog(
        user_id=actor_id, entity_type=entity_type, entity_id=str(entity_id),
        action=f"{entity_type}.{action}",
        old_value=json.dumps(before, ensure_ascii=False) if before is not None else None,
        new_value=json.dumps(after, ensure_ascii=False) if after is not None else None,
    ))
