import asyncio

from celery import shared_task
from pymongo.errors import ConnectionFailure
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database import DATABASE_URL, get_mongo_database
from src.models.experiment import Experiment, ExperimentStatus
from src.services.experiment_results_service import calculate_results
from src.services.experiment_service import ExperimentConflictError, ExperimentNotFoundError


async def _calculate_running() -> dict[str, str]:
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    outcomes = {}
    try:
        async with sessions() as db:
            ids = list(await db.scalars(
                select(Experiment.id).where(Experiment.status == ExperimentStatus.RUNNING)
            ))
        for experiment_id in ids:
            try:
                async with sessions.begin() as db:
                    await calculate_results(db, get_mongo_database(), experiment_id)
                outcomes[str(experiment_id)] = "calculated"
            except (ExperimentConflictError, ExperimentNotFoundError) as error:
                outcomes[str(experiment_id)] = str(error)
    finally:
        await engine.dispose()
    return outcomes


@shared_task(
    name="analytics.calculate_statistics",
    autoretry_for=(ConnectionFailure, OperationalError),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def calculate_statistics() -> dict[str, str]:
    """Пересчитать запущенные эксперименты, сохранив их текущий статус."""
    return asyncio.run(_calculate_running())
