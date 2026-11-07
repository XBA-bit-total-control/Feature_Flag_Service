from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class ExperimentResultResponse(BaseModel):
    """Последний расчёт варианта, сравнение направлено от control к treatment."""

    experiment_id: UUID
    variant_name: str
    sample_size: int
    conversions: int
    conversion_rate: float
    z_statistic: float | None
    p_value: float | None
    confidence_interval_lower: float | None
    confidence_interval_upper: float | None
    is_significant: bool
    enough_data: bool
    created_at: datetime

    model_config = {"from_attributes": True}
