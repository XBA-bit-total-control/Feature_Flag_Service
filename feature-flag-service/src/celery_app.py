from celery import Celery
from celery.exceptions import TimeoutError
from kombu.exceptions import OperationalError

from src.config import settings


app = Celery(
    "feature_flag_service",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["src.tasks.event_tasks", "src.tasks.statistics_tasks", "src.tasks.report_tasks"],
)
app.conf.update(
    task_default_queue="events",
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    broker_connection_timeout=3,
    broker_connection_retry_on_startup=False,
    task_routes={
        "analytics.calculate_statistics": {"queue": "analytics"},
        "reports.*": {"queue": "reports"},
    },
    beat_schedule={
        "cleanup-old-events": {
            "task": "events.cleanup_old", "schedule": 86400.0,
        },
        "pending-reports": {
            "task": "reports.process_pending", "schedule": 60.0,
        },
        "calculate-statistics": {
            "task": "analytics.calculate_statistics",
            "schedule": 1800.0,
        },
    },
)


def check_worker() -> None:
    """Отправить диагностическую задачу и дождаться ответа воркера."""
    try:
        result = app.send_task("events.ping", retry=False)
        print(result.get(timeout=10))
    except OperationalError as error:
        raise SystemExit("Не удалось подключиться к брокеру Celery.") from error
    except TimeoutError as error:
        raise SystemExit("Воркер не ответил за 10 секунд.") from error


if __name__ == "__main__":
    check_worker()
