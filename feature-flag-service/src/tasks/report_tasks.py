import asyncio
from uuid import UUID

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.database import DATABASE_URL
from src.models.experiment_report import ExperimentReport
from src.services.experiment_reports_service import complete_report


async def _run_reports(report_id: UUID | None = None) -> None:
    engine = create_async_engine(DATABASE_URL)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if report_id is not None:
            ids = [report_id]
        else:
            async with sessions() as db:
                ids = list(await db.scalars(
                    select(ExperimentReport.id)
                    .where(ExperimentReport.status == "pending")
                    .order_by(ExperimentReport.created_at, ExperimentReport.id).limit(100)
                ))
        for item in ids:
            async with sessions.begin() as db:
                await complete_report(db, item)
    finally:
        await engine.dispose()


@shared_task(
    name="reports.generate", acks_late=True, reject_on_worker_lost=True,
    autoretry_for=(OperationalError,), retry_backoff=True,
    retry_kwargs={"max_retries": 3}, ignore_result=True,
)
def generate_report_task(report_id: str) -> None:
    """Выполнить сохранённое задание отчёта."""
    asyncio.run(_run_reports(UUID(report_id)))


@shared_task(name="reports.process_pending", ignore_result=True)
def process_pending_reports() -> None:
    """Подобрать задания после пропуска или сбоя публикации."""
    asyncio.run(_run_reports())
