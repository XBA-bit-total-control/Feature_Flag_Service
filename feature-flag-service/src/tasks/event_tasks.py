from datetime import datetime, timezone

from celery import shared_task
from pymongo.errors import ConnectionFailure

from src.database import get_mongo_database


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
