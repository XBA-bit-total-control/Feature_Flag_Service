from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi.concurrency import run_in_threadpool
from pydantic import ValidationError
from pymongo.collection import Collection
from pymongo.database import Database
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.experiment import Experiment
from src.models.experiment_participant import ExperimentParticipant
from src.services.experiment_results_service import parse_stored_event
from src.services.experiment_service import ACTIVE_STATUSES, TERMINAL_STATUSES
from src.services.statistics_service import summarize_participants


BATCH_SIZE = 1000
MAX_BATCHES_PER_EXPERIMENT = 10


def _read_batch(
    collection: Collection[dict[str, Any]], filters: dict[str, Any],
) -> list[dict[str, Any]]:
    return list(collection.find(filters).sort("timestamp", 1).limit(BATCH_SIZE))


async def _preserve_participants(
    db: AsyncSession, experiment: Experiment, documents: list[dict[str, Any]],
) -> None:
    participants = summarize_participants(
        (parse_stored_event(document) for document in documents),
        experiment.id, experiment.primary_metric,
    )
    rows = [dict(
        experiment_id=experiment.id, user_id=item.user_id, variant_key=item.variant_key,
        impression_at=item.impression_at, conversion_at=item.conversion_at,
    ) for item in participants]
    if not rows:
        return
    statement = insert(ExperimentParticipant).values(rows)
    await db.execute(statement.on_conflict_do_update(
        index_elements=["experiment_id", "user_id", "variant_key"],
        set_={
            "impression_at": func.least(ExperimentParticipant.impression_at, statement.excluded.impression_at),
            "conversion_at": func.greatest(ExperimentParticipant.conversion_at, statement.excluded.conversion_at),
        },
    ))


async def _clean_experiment(
    sessions: async_sessionmaker[AsyncSession], collection: Collection[dict[str, Any]],
    experiment_id: UUID, now: datetime,
) -> tuple[int, int, bool]:
    active_removed = terminal_removed = 0
    for _ in range(MAX_BATCHES_PER_EXPERIMENT):
        async with sessions.begin() as db:
            experiment = await db.get(Experiment, experiment_id, with_for_update=True)
            if experiment is None:
                break
            active = experiment.status in ACTIVE_STATUSES
            filters: dict[str, Any] = {"experiment_id": str(experiment_id)}
            if active:
                filters.update(processed=True, timestamp={"$lt": now - timedelta(days=30)})
            elif experiment.status in TERMINAL_STATUSES:
                cutoff = now - timedelta(days=90)
                filters["timestamp"] = {"$lt": cutoff}
                if experiment.end_date is not None and experiment.end_date < cutoff:
                    await db.execute(delete(ExperimentParticipant).where(
                        ExperimentParticipant.experiment_id == experiment_id,
                    ))
            else:
                break
            documents = await run_in_threadpool(_read_batch, collection, filters)
            if active and documents:
                try:
                    await _preserve_participants(db, experiment, documents)
                except ValidationError:
                    return active_removed, terminal_removed, True

        if not documents:
            break
        # Удаляем только прочитанную пачку после фиксации состояния в PostgreSQL.
        result = await run_in_threadpool(collection.delete_many, {
            **filters, "_id": {"$in": [document["_id"] for document in documents]},
        })
        if active:
            active_removed += result.deleted_count
        else:
            terminal_removed += result.deleted_count
    return active_removed, terminal_removed, False


async def cleanup_old_events(
    sessions: async_sessionmaker[AsyncSession], mongo: Database[dict[str, Any]], now: datetime,
) -> dict[str, int]:
    """Удалить просроченные события, сохранив статистику активных экспериментов."""
    async with sessions() as db:
        experiment_ids = list(await db.scalars(select(Experiment.id).where(
            Experiment.status.in_((*ACTIVE_STATUSES, *TERMINAL_STATUSES)),
        )))
    totals = {"active_removed": 0, "terminal_removed": 0, "invalid_experiments": 0}
    for experiment_id in experiment_ids:
        active_removed, terminal_removed, invalid = await _clean_experiment(
            sessions, mongo["events"], experiment_id, now,
        )
        totals["active_removed"] += active_removed
        totals["terminal_removed"] += terminal_removed
        totals["invalid_experiments"] += int(invalid)
    return totals
