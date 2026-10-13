from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    """Схема для создания пользователя."""

    email: EmailStr
    password: str


class UserResponse(BaseModel):
    """Схема ответа с данными пользователя."""

    id: str
    email: str

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    """Схема ответа с JWT токеном."""

    access_token: str
    token_type: str = "bearer"
