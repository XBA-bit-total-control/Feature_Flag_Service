from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, Field, model_validator


class ResolveRequest(BaseModel):
    """Запрос определения доступности флага и варианта эксперимента."""

    flag_key: str = Field(min_length=1, max_length=255)
    user_id: str = Field(
        min_length=1, max_length=255,
        validation_alias=AliasChoices("user_id", "user_identifier"),
    )
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def validate_user_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict) and "user_id" in value and "user_identifier" in value:
            if value["user_id"] != value["user_identifier"]:
                raise ValueError("Идентификаторы пользователя не совпадают")
            value = {key: item for key, item in value.items() if key != "user_identifier"}
        return value

    model_config = {"extra": "forbid"}


class ResolveResponse(BaseModel):
    """Доступность флага, причина и выбранный вариант."""

    flag_key: str
    user_id: str
    enabled: bool
    variant: str | None = None
    variant_key: str = "control"
    reason: str
    experiment_id: UUID | None = None
