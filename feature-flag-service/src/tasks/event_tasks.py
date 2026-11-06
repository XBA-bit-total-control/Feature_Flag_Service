from celery import shared_task


@shared_task(name="events.ping")
def ping() -> str:
    """Подтвердить получение задачи воркером."""
    return "pong"
