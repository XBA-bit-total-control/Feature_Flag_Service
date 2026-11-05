from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    """Схема для создания пользователя."""

    email: EmailStr = Field(..., description="Email")
    password: str = Field(
        ...,
        min_length=8,
        max_length=72,
        description="Пароль"
    )

    @field_validator("password")
    def validate_password(cls, value: str) -> str:
        """Проверка что пароль не превышает 72 байта в UTF-8."""
        if len(value.encode("utf-8")) > 72:
            raise ValueError(
                "Пароль не должен превышать 72 байта в кодировке UTF-8"
            )
        return value


class UserResponse(BaseModel):
    """Схема ответа с данными пользователя."""

    id: UUID = Field(..., description="Идентификатор пользователя")
    email: str = Field(..., description="Email")

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """Схема ответа с JWT токеном."""

    access_token: str = Field(..., description="JWT токен")
    token_type: str = "bearer"
