import asyncio
from datetime import datetime, timezone

from celery import shared_task
from pymongo.errors import ConnectionFailure
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database import DATABASE_URL, get_mongo_database
from src.services.event_retention import cleanup_old_events


@shared_task(name="events.ping")
def ping() -> str:
    """Подтвердить получение задачи воркером."""
    return "pong"


@shared_task(
    name="events.process_pending",
    autoretry_for=(ConnectionFailure,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
)
def process_pending_events() -> int:
    """Отметить до 1000 сохранённых событий как обработанные."""
    collection = get_mongo_database()["events"]
    pending = list(
        collection.find({"processed": False}, {"_id": 1}).sort("_id").limit(1000)
    )
    processed_count = 0
    for event in pending:
        result = collection.update_one(
            {"_id": event["_id"], "processed": False},
            {"$set": {"processed": True, "processed_at": datetime.now(timezone.utc)}},
        )
        processed_count += result.modified_count
    return processed_count


@shared_task(
    name="events.cleanup_old",
    autoretry_for=(ConnectionFailure, OperationalError),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
    reject_on_worker_lost=True,
)
def cleanup_old_events_task() -> dict[str, int]:
    """Очистить устаревшие события и вернуть число удалённых документов."""

    async def run() -> dict[str, int]:
        engine = create_async_engine(DATABASE_URL)
        try:
            return await cleanup_old_events(
                async_sessionmaker(engine), get_mongo_database(), datetime.now(timezone.utc),
            )
        finally:
            await engine.dispose()

    return asyncio.run(run())
