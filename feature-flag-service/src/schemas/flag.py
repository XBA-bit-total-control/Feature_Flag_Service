from uuid import UUID

from pydantic import BaseModel, Field


class FlagCreate(BaseModel):
    """Схема для создания feature-флага."""

    key: str = Field(..., min_length=1, max_length=255, description="Уникальный ключ флага")
    description: str | None = Field(None, max_length=1000, description="Описание флага")
    type: str = Field(default="simple", max_length=50, description="Тип флага (simple, ab)")
    enabled: bool = Field(default=False, description="Активирован ли флаг")
    rollout_percentage: int = Field(default=100, ge=0, le=100, description="Процент rollout (0-100)")
    salt: str = Field(default="", max_length=100, description="Salt для детерминированного хеширования")
    owner_id: UUID = Field(description="ID владельца флага")


class FlagUpdate(BaseModel):
    """Схема для обновления feature-флага."""

    key: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = Field(None, max_length=1000)
    type: str | None = Field(None, max_length=50)
    enabled: bool | None = None
    rollout_percentage: int | None = Field(None, ge=0, le=100)
    salt: str | None = Field(None, max_length=100)
    owner_id: UUID | None = None


class FlagResponse(BaseModel):
    """Ответ API с данными флага."""

    id: UUID
    key: str
    description: str | None
    type: str
    enabled: bool
    rollout_percentage: int
    salt: str
    owner_id: UUID
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}