from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel


class TemplateReportResponse(BaseModel):
    """Шаблонный отчёт по последним сохранённым результатам."""

    report_content: str
    report_type: Literal["template"] = "template"


class ExperimentReportResponse(BaseModel):
    """Состояние задания или готовый текст отчёта эксперимента."""

    id: UUID
    experiment_id: UUID
    status: str
    report_type: str
    report_content: str | None
    llm_model: str | None
    tokens_used: int | None
    fallback_reason: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
