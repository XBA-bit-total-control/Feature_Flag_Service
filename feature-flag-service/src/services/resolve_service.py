from math import isfinite

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.experiment import Experiment, ExperimentStatus
from src.schemas.resolve import ResolveRequest, ResolveResponse
from src.services import flag_service
from src.utils.hashing import deterministic_hash, resolve_bucket


class ResolveNotFoundError(ValueError):
    """Запрошенный флаг не найден."""


class ResolveConfigurationError(ValueError):
    """Конфигурация флага не позволяет определить вариант."""


async def resolve_flag(db: AsyncSession, data: ResolveRequest) -> ResolveResponse:
    """Определить доступность флага и назначение в активном эксперименте."""
    flag = await flag_service.get_flag_by_key(db, data.flag_key)
    if flag is None:
        raise ResolveNotFoundError("Флаг не найден")
    response = ResolveResponse(
        flag_key=flag.key, user_id=data.user_id, enabled=False, reason="disabled"
    )
    if not flag.enabled:
        return response
    if flag.type in ("simple", "boolean", "percentage"):
        if not 0 <= flag.rollout_percentage <= 100:
            raise ResolveConfigurationError("Процент rollout должен быть от 0 до 100")
        response.enabled = (
            deterministic_hash(data.user_id, flag.key, flag.salt) < flag.rollout_percentage
        )
        response.reason = "rollout"
        return response
    if flag.type not in ("ab", "ab_test"):
        raise ResolveConfigurationError("Неизвестный тип флага")

    experiments = list(await db.scalars(
        select(Experiment).where(
            Experiment.flag_id == flag.id, Experiment.status == ExperimentStatus.RUNNING
        )
    ))
    if not experiments:
        response.reason = "no_active_experiment"
        return response
    if len(experiments) != 1:
        raise ResolveConfigurationError("Для флага запущено несколько экспериментов")
    experiment = experiments[0]
    variants = sorted(
        await flag_service.get_variants(db, flag.id), key=lambda variant: variant.variant_key
    )
    if not variants:
        response.reason = "no_variants"
        return response
    weights = [variant.weight for variant in variants]
    if (
        len(variants) < 2
        or len({variant.variant_key for variant in variants}) != len(variants)
        or any(not isfinite(weight) or weight < 0 for weight in weights)
        or not isfinite(sum(weights))
        or sum(weights) <= 0
    ):
        raise ResolveConfigurationError("Некорректные варианты или веса эксперимента")
    bucket = deterministic_hash(data.user_id, str(experiment.id), flag.salt)
    variant = variants[resolve_bucket(bucket, weights)]
    response.enabled = True
    response.variant = response.variant_key = variant.variant_key
    response.experiment_id = experiment.id
    response.reason = "ab_test"
    return response
