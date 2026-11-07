from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.schemas.resolve import ResolveRequest
from src.services import resolve_service


@pytest.mark.asyncio
@pytest.mark.parametrize("percentage, expected", [(0, False), (100, True)])
async def test_rollout_boundaries(percentage, expected, monkeypatch) -> None:
    """Граничные проценты дают выключенный и включённый флаг."""
    flag = SimpleNamespace(
        key="checkout", enabled=True, type="simple", rollout_percentage=percentage,
        salt="salt",
    )
    monkeypatch.setattr(
        resolve_service.flag_service, "get_flag_by_key", AsyncMock(return_value=flag)
    )
    response = await resolve_service.resolve_flag(
        AsyncMock(), ResolveRequest(flag_key="checkout", user_id="u1")
    )
    assert response.enabled is expected, (
        f"Для rollout {percentage} ожидалось {expected}, получено {response.enabled}"
    )


@pytest.mark.asyncio
async def test_ab_assignment_is_stable_and_requires_running_experiment(monkeypatch) -> None:
    """Порядок вариантов не меняет назначение, отсутствие эксперимента выключает флаг."""
    flag = SimpleNamespace(id=uuid4(), key="checkout", enabled=True, type="ab", salt="s")
    experiment = SimpleNamespace(id=uuid4())
    variants = [SimpleNamespace(variant_key=key, weight=1) for key in ("A", "B")]
    monkeypatch.setattr(
        resolve_service.flag_service, "get_flag_by_key", AsyncMock(return_value=flag)
    )
    get_variants = AsyncMock(side_effect=[variants, list(reversed(variants))])
    monkeypatch.setattr(resolve_service.flag_service, "get_variants", get_variants)
    db = AsyncMock()
    db.scalars.return_value = [experiment]
    data = ResolveRequest(flag_key="checkout", user_id="u1")
    first = await resolve_service.resolve_flag(db, data)
    second = await resolve_service.resolve_flag(db, data)
    assert first.variant == second.variant and first.experiment_id == experiment.id, (
        "Повторное определение варианта изменило назначение или ID эксперимента"
    )
    db.scalars.return_value = []
    missing = await resolve_service.resolve_flag(db, data)
    assert not missing.enabled and missing.reason == "no_active_experiment", (
        f"Без запущенного эксперимента флаг должен быть выключен: {missing}"
    )


def test_request_accepts_alias_and_rejects_conflicts() -> None:
    """Оба имени ID поддерживаются, противоречивые значения отклоняются."""
    data = ResolveRequest(flag_key="checkout", user_identifier="u1")
    assert data.user_id == "u1", "user_identifier не преобразован в user_id"
    with pytest.raises(ValidationError):
        ResolveRequest(flag_key="checkout", user_id="u1", user_identifier="u2")
    with pytest.raises(ValidationError):
        ResolveRequest(flag_key="checkout", user_id="")
