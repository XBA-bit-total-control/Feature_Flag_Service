from typing import Any
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from pymongo.database import Database
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.experiment import Experiment, ExperimentStatus
from src.models.experiment_result import ExperimentResult
from src.models.flag_variant import FlagVariant
from src.schemas.event import EventCreate
from src.services.experiment_service import ExperimentConflictError, ExperimentNotFoundError
from src.services.statistics_service import compare_conversions, count_participants


def _read_events(mongo: Database[dict[str, Any]], experiment: Experiment) -> list[EventCreate]:
    fields = set(EventCreate.model_fields)
    return [
        EventCreate.model_validate({key: value for key, value in doc.items() if key in fields})
        for doc in mongo["events"].find({"experiment_id": str(experiment.id)})
    ]


async def get_results(db: AsyncSession, experiment_id: UUID) -> list[ExperimentResult]:
    """Прочитать последний сохранённый снимок результатов эксперимента."""
    return list(await db.scalars(
        select(ExperimentResult)
        .where(ExperimentResult.experiment_id == experiment_id)
        .order_by(ExperimentResult.variant_name)
    ))


async def calculate_results(
        db: AsyncSession, mongo: Database[dict[str, Any]], experiment_id: UUID
) -> list[ExperimentResult]:
    """Заменить последний снимок результатов в одной транзакции PostgreSQL."""
    experiment = await db.get(Experiment, experiment_id, with_for_update=True, populate_existing=True)
    if experiment is None:
        raise ExperimentNotFoundError("Эксперимент не найден")
    if experiment.status not in (ExperimentStatus.RUNNING, ExperimentStatus.PAUSED):
        raise ExperimentConflictError("Расчёт доступен запущенному или приостановленному эксперименту")
    keys = list(await db.scalars(
        select(FlagVariant.variant_key).where(FlagVariant.flag_id == experiment.flag_id)
    ))
    if len(keys) != 2 or len(set(keys)) != 2:
        raise ExperimentConflictError("Для расчёта требуются ровно два разных варианта")
    control = "control" if "control" in keys else "A" if "A" in keys else None
    if control is None:
        raise ExperimentConflictError("Назовите контрольный вариант control или A")
    treatment = next(key for key in keys if key != control)
    try:
        events = await run_in_threadpool(_read_events, mongo, experiment)
        counts = count_participants(
            events, experiment.id, experiment.primary_metric, control, treatment
        )
        comparison = compare_conversions(
            *counts, alpha=experiment.alpha, min_sample_size=experiment.min_sample_size
        )
    except ValueError as error:
        raise ExperimentConflictError(str(error)) from error

    await db.execute(delete(ExperimentResult).where(
        ExperimentResult.experiment_id == experiment.id
    ))
    interval = comparison.confidence_interval or (None, None)
    results = [
        ExperimentResult(
            experiment_id=experiment.id, variant_name=key,
            sample_size=count.sample_size, conversions=count.conversions,
            conversion_rate=count.conversion_rate or 0.0,
            z_statistic=comparison.z_statistic, p_value=comparison.p_value,
            confidence_interval_lower=interval[0], confidence_interval_upper=interval[1],
            is_significant=comparison.is_significant, enough_data=comparison.enough_data,
        )
        for key, count in zip((control, treatment), counts)
    ]
    db.add_all(results)
    await db.flush()
    for result in results:
        await db.refresh(result)
    return results
