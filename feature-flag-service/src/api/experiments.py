from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.dependencies import get_current_user, get_db
from src.models.experiment import Experiment, ExperimentStatus
from src.models.user import User
from src.schemas.experiment import (
    ExperimentCreate,
    ExperimentResponse,
    ExperimentStatusUpdate,
    ExperimentUpdate,
)
from src.services import experiment_service


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
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Experiment]:
    """Получить свои эксперименты с фильтрами по статусу и флагу."""
    return await experiment_service.list_experiments(db, user.id, status, flag_id)


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
