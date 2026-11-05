from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue, field_validator


class EventCreate(BaseModel):
    """Схема для приёма события от клиента."""

    event_type: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Тип события (impression, click, conversion, custom)",
    )
    flag_key: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Ключ feature-флага",
    )
    user_id: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Идентификатор пользователя",
    )
    experiment_id: UUID | None = Field(
        None,
        description="ID эксперимента (если есть)",
    )
    variant_key: str | None = Field(
        None,
        max_length=100,
        description="Ключ варианта (A, B, control, treatment)",
    )
    event_name: str | None = Field(
        None,
        max_length=255,
        description="Название события (например checkout_completed)",
    )
    value: float | None = Field(
        None,
        ge=0,
        description="Значение события (например 1 для конверсии)",
    )
    metadata: dict[str, JsonValue] | None = Field(
        None,
        description="Дополнительный контекст (country, platform, session_id)",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Временная метка события",
    )

    @field_validator("timestamp")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        """Привести дату к UTC, считая дату без часового пояса временем UTC."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    model_config = {
        "from_attributes": True,
        "extra": "forbid",
        "allow_inf_nan": False,
    }


class EventResponse(BaseModel):
    """Ответ API с данными события."""

    id: str
    event_type: str
    flag_key: str
    user_id: str
    experiment_id: UUID | None
    variant_key: str | None
    event_name: str | None
    value: float | None
    metadata: dict[str, JsonValue] | None
    timestamp: datetime

    model_config = {"from_attributes": True}


class EventBatchCreate(BaseModel):
    """Схема для пакетной отправки событий."""

    events: list[EventCreate] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Список событий для отправки (1-100)",
    )


class EventBatchResponse(BaseModel):
    """Ответ API на пакетную отправку."""

    accepted: int = Field(description="Принято событий")
    rejected: int = Field(description="Отклонено событий")
