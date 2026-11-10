from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.experiment import Experiment, ExperimentStatus
from src.models.flag import Flag
from src.schemas.experiment import ExperimentCreate, ExperimentUpdate


class ExperimentNotFoundError(ValueError):
    """Эксперимент или связанный флаг не найден."""


class ExperimentAccessError(ValueError):
    """Пользователь не владеет ресурсом."""


class ExperimentConflictError(ValueError):
    """Операция конфликтует с текущим состоянием эксперимента."""


ALLOWED_TRANSITIONS = {
    ExperimentStatus.DRAFT: {ExperimentStatus.RUNNING},
    ExperimentStatus.RUNNING: {
        ExperimentStatus.PAUSED, ExperimentStatus.COMPLETED, ExperimentStatus.STOPPED,
    },
    ExperimentStatus.PAUSED: {
        ExperimentStatus.RUNNING, ExperimentStatus.COMPLETED, ExperimentStatus.STOPPED,
    },
}
ACTIVE_STATUSES = (ExperimentStatus.RUNNING, ExperimentStatus.PAUSED)
TERMINAL_STATUSES = (ExperimentStatus.COMPLETED, ExperimentStatus.STOPPED)


async def _flush_experiment(db: AsyncSession, experiment: Experiment) -> Experiment:
    try:
        await db.flush()
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            if getattr(error.orig.__cause__, "constraint_name", None) == "uq_experiments_active_flag":
                raise ExperimentConflictError(
                    "У флага уже есть запущенный или приостановленный эксперимент"
                ) from error
            raise ExperimentConflictError(
                "Эксперимент с таким именем уже существует"
            ) from error
        if getattr(error.orig, "sqlstate", None) == "23503":
            raise ExperimentNotFoundError(
                "Связанный флаг или пользователь не найден"
            ) from error
        raise
    await db.refresh(experiment)
    return experiment


async def create_experiment(
    db: AsyncSession, data: ExperimentCreate, user_id: UUID
) -> Experiment:
    flag = await db.get(Flag, data.flag_id)
    if flag is None:
        raise ExperimentNotFoundError("Флаг не найден")
    if flag.owner_id != user_id:
        raise ExperimentAccessError("Создавать эксперименты может только владелец флага")
    experiment = Experiment(**data.model_dump(), creator_id=user_id)
    db.add(experiment)
    return await _flush_experiment(db, experiment)


async def list_experiments(
    db: AsyncSession, user_id: UUID, status: ExperimentStatus | None, flag_id: UUID | None,
    *, limit: int, offset: int,
) -> list[Experiment]:
    query = select(Experiment).where(Experiment.creator_id == user_id)
    if status is not None:
        query = query.where(Experiment.status == status)
    if flag_id is not None:
        query = query.where(Experiment.flag_id == flag_id)
    result = await db.scalars(
        query.order_by(Experiment.created_at.desc(), Experiment.id).offset(offset).limit(limit)
    )
    return list(result)


async def get_experiment(
    db: AsyncSession, experiment_id: UUID, user_id: UUID, *, lock: bool = False
) -> Experiment:
    experiment = await db.get(Experiment, experiment_id, with_for_update=lock)
    if experiment is None:
        raise ExperimentNotFoundError("Эксперимент не найден")
    if experiment.creator_id != user_id:
        raise ExperimentAccessError("Эксперимент доступен только его создателю")
    return experiment


async def _get_draft(
    db: AsyncSession, experiment_id: UUID, user_id: UUID
) -> Experiment:
    experiment = await get_experiment(db, experiment_id, user_id, lock=True)
    if experiment.status != ExperimentStatus.DRAFT:
        raise ExperimentConflictError("Изменять или удалять можно только черновик")
    return experiment


async def update_experiment(
    db: AsyncSession, experiment_id: UUID, user_id: UUID, data: ExperimentUpdate
) -> Experiment:
    experiment = await _get_draft(db, experiment_id, user_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(experiment, field, value)
    return await _flush_experiment(db, experiment)


async def change_status(
    db: AsyncSession, experiment_id: UUID, user_id: UUID, status: ExperimentStatus
) -> Experiment:
    experiment = await get_experiment(db, experiment_id, user_id, lock=True)
    if status == experiment.status:
        return experiment
    if status not in ALLOWED_TRANSITIONS.get(experiment.status, ()):
        raise ExperimentConflictError(
            f"Переход из {experiment.status} в {status} недопустим"
        )

    if status == ExperimentStatus.RUNNING:
        # Запуски экспериментов одного флага проверяются последовательно.
        await db.get(Flag, experiment.flag_id, with_for_update=True)
        active_id = await db.scalar(
            select(Experiment.id)
            .where(
                Experiment.flag_id == experiment.flag_id,
                Experiment.id != experiment.id,
                Experiment.status.in_(ACTIVE_STATUSES),
            )
            .limit(1)
        )
        if active_id is not None:
            raise ExperimentConflictError(
                "У флага уже есть запущенный или приостановленный эксперимент"
            )
        if experiment.start_date is None:
            experiment.start_date = datetime.now(timezone.utc)
    if status in TERMINAL_STATUSES:
        experiment.end_date = datetime.now(timezone.utc)

    experiment.status = status
    return await _flush_experiment(db, experiment)


async def delete_experiment(
    db: AsyncSession, experiment_id: UUID, user_id: UUID
) -> None:
    experiment = await _get_draft(db, experiment_id, user_id)
    await db.delete(experiment)
    await db.flush()
