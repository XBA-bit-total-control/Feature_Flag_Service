from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from src.models.experiment import ExperimentStatus


class ExperimentCreate(BaseModel):
    """Схема создания черновика эксперимента."""

    flag_id: UUID
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    primary_metric: str = Field(min_length=1, max_length=255)
    alpha: float = Field(0.05, gt=0, lt=1)
    min_sample_size: int | None = Field(None, gt=0, le=2 ** 31 - 1, strict=True)

    model_config = {"extra": "forbid", "str_strip_whitespace": True}


class ExperimentUpdate(BaseModel):
    """Схема изменения параметров эксперимента."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    primary_metric: str | None = Field(None, min_length=1, max_length=255)
    alpha: float | None = Field(None, gt=0, lt=1)
    min_sample_size: int | None = Field(None, gt=0, le=2 ** 31 - 1, strict=True)

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Укажите хотя бы одно поле для изменения")
        for field in ("name", "primary_metric", "alpha"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"Поле {field} не может быть null")
        return self


class ExperimentStatusUpdate(BaseModel):
    """Запрос перехода эксперимента в другой статус."""

    status: ExperimentStatus

    model_config = {"extra": "forbid"}


class ExperimentResponse(BaseModel):
    """Ответ API с параметрами и состоянием эксперимента."""

    id: UUID
    flag_id: UUID
    creator_id: UUID
    name: str
    description: str | None
    status: ExperimentStatus
    primary_metric: str
    alpha: float
    min_sample_size: int | None
    start_date: datetime | None
    end_date: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
