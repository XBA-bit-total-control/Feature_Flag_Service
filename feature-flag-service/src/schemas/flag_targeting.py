from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, StrictBool, StrictInt, StrictStr, StringConstraints, model_validator


TargetingValue = StrictStr | StrictBool | StrictInt | Annotated[
    float, Field(strict=True, allow_inf_nan=False)
]


class FlagTargetingWrite(BaseModel):
    """Данные для создания или полной замены правила таргетинга."""

    attribute: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
    operator: Literal["eq", "ne", "in", "not_in", "gt", "lt", "gte", "lte"]
    values: list[TargetingValue] = Field(min_length=1)
    is_enabled: StrictBool = True

    @model_validator(mode="after")
    def validate_values(self) -> Self:
        if self.operator not in ("in", "not_in") and len(self.values) != 1:
            raise ValueError("Оператор требует ровно одно значение")
        if self.operator in ("gt", "lt", "gte", "lte") and type(self.values[0]) not in (int, float):
            raise ValueError("Для сравнения требуется число")
        if len({(isinstance(value, bool), value) for value in self.values}) != len(self.values):
            raise ValueError("Список содержит повторяющиеся значения")
        return self

    model_config = {"extra": "forbid", "from_attributes": True}


class FlagTargetingResponse(BaseModel):
    """Сохранённое правило таргетинга."""

    id: UUID
    flag_id: UUID
    attribute: str
    operator: str
    values: list[TargetingValue]
    is_enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
