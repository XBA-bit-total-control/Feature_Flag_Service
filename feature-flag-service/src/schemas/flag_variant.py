from uuid import UUID

from pydantic import BaseModel, Field


class FlagVariantCreate(BaseModel):
    """Схема для создания варианта A/B теста."""

    variant_key: str = Field(..., min_length=1, max_length=100, description="Уникальный ключ варианта")
    description: str | None = Field(None, max_length=1000, description="Описание варианта")
    weight: float = Field(default=1.0, gt=0, description="Вес варианта (должен быть > 0)")
    flag_id: UUID = Field(description="ID флага, к которому относится вариант")


class FlagVariantResponse(BaseModel):
    """Ответ API с данными варианта."""

    id: UUID
    variant_key: str
    description: str | None
    weight: float
    flag_id: UUID
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}
