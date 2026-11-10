from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from kombu.exceptions import OperationalError as BrokerError
from pymongo.database import Database
from pymongo.errors import PyMongoError
from sqlalchemy.ext.asyncio import AsyncSession

from src.celery_app import app as celery_app
from src.database import get_mongo_database
from src.dependencies import get_current_user, get_db
from src.models.experiment import Experiment, ExperimentStatus
from src.models.experiment_report import ExperimentReport
from src.models.experiment_result import ExperimentResult
from src.models.user import User
from src.schemas.experiment import (
    ExperimentCreate,
    ExperimentResponse,
    ExperimentStatusUpdate,
    ExperimentUpdate,
)
from src.schemas.experiment_report import ExperimentReportResponse, TemplateReportResponse
from src.schemas.experiment_result import ExperimentResultResponse
from src.services import experiment_service
from src.services.experiment_reports_service import (
    build_template_report, list_reports, request_llm_report,
)
from src.services.experiment_results_service import calculate_results, get_results


router = APIRouter()


@contextmanager
def _experiment_errors() -> Iterator[None]:
    try:
        yield
    except experiment_service.ExperimentNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except experiment_service.ExperimentAccessError as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except experiment_service.ExperimentConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("", response_model=ExperimentResponse, status_code=201)
async def create_experiment(
        data: ExperimentCreate,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
) -> Experiment:
    """Создать черновик эксперимента для своего флага."""
    with _experiment_errors():
        return await experiment_service.create_experiment(db, data, user.id)


@router.get("", response_model=list[ExperimentResponse])
async def list_experiments(
        status: ExperimentStatus | None = None,
        flag_id: UUID | None = None,
        limit: int = Query(default=100, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=2**31 - 1),
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
) -> list[Experiment]:
    """Получить свои эксперименты с фильтрами по статусу и флагу."""
    return await experiment_service.list_experiments(
        db, user.id, status, flag_id, limit=limit, offset=offset,
    )


@router.get("/{experiment_id}", response_model=ExperimentResponse)
async def get_experiment(
        experiment_id: UUID,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
) -> Experiment:
    """Получить параметры и состояние эксперимента."""
    with _experiment_errors():
        return await experiment_service.get_experiment(db, experiment_id, user.id)


@router.patch("/{experiment_id}", response_model=ExperimentResponse)
async def update_experiment(
        experiment_id: UUID,
        data: ExperimentUpdate | ExperimentStatusUpdate,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
) -> Experiment:
    """Изменить параметры черновика или выполнить переход статуса."""
    with _experiment_errors():
        if isinstance(data, ExperimentStatusUpdate):
            return await experiment_service.change_status(
                db, experiment_id, user.id, data.status
            )
        return await experiment_service.update_experiment(
            db, experiment_id, user.id, data
        )


@router.delete("/{experiment_id}", status_code=204)
async def delete_experiment(
        experiment_id: UUID,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
) -> None:
    """Удалить черновик эксперимента."""
    with _experiment_errors():
        await experiment_service.delete_experiment(db, experiment_id, user.id)


@router.get("/{experiment_id}/results", response_model=list[ExperimentResultResponse])
async def read_results(
        experiment_id: UUID,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
) -> list[ExperimentResult]:
    """Получить последний сохранённый снимок результатов своего эксперимента."""
    with _experiment_errors():
        await experiment_service.get_experiment(db, experiment_id, user.id)
        return await get_results(db, experiment_id)


@router.post("/{experiment_id}/results", response_model=list[ExperimentResultResponse])
async def recalculate_results(
        experiment_id: UUID,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
        mongo: Database[dict[str, Any]] = Depends(get_mongo_database),
) -> list[ExperimentResult]:
    """Пересчитать результаты своего эксперимента по сохранённым событиям."""
    with _experiment_errors():
        await experiment_service.get_experiment(db, experiment_id, user.id)
        try:
            return await calculate_results(db, mongo, experiment_id)
        except PyMongoError as error:
            raise HTTPException(status_code=503, detail="Хранилище событий недоступно") from error


@router.get("/{experiment_id}/template/reports", response_model=TemplateReportResponse)
async def read_template_report(
        experiment_id: UUID,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
) -> TemplateReportResponse:
    """Получить шаблонный отчёт для своего эксперимента."""
    with _experiment_errors():
        await experiment_service.get_experiment(db, experiment_id, user.id)
        content = await build_template_report(db, experiment_id)
        return TemplateReportResponse(report_content=content)


@router.post("/{experiment_id}/llm/reports", response_model=ExperimentReportResponse, status_code=202)
async def create_llm_report(
        experiment_id: UUID,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
) -> ExperimentReport:
    """Поставить генерацию LLM-отчёта для своего эксперимента в очередь."""
    with _experiment_errors():
        await experiment_service.get_experiment(db, experiment_id, user.id)
        report, created = await request_llm_report(db, experiment_id)
        if not created:
            return report
        # Воркер должен видеть задание до получения сообщения RabbitMQ.
        await db.commit()
        try:
            await run_in_threadpool(
                celery_app.send_task, "reports.generate", args=[str(report.id)],
                retry=False, ignore_result=True,
            )
        except BrokerError:
            pass
        return report


@router.get("/{experiment_id}/reports", response_model=list[ExperimentReportResponse])
async def read_reports(
        experiment_id: UUID,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
) -> list[ExperimentReport]:
    """Получить историю отчётов своего эксперимента."""
    with _experiment_errors():
        await experiment_service.get_experiment(db, experiment_id, user.id)
        return await list_reports(db, experiment_id)
