from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_async_session
from src.dependencies import get_current_user
from src.models.user import User, UserRole
from src.schemas.user import UserCreate, UserResponse, TokenResponse
from src.utils.jwt_handler import hash_password, create_access_token

router = APIRouter()


@router.post(
    "/auth/register",
    response_model=UserResponse,
    status_code=201,
)
async def register(
    data: UserCreate,
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """Зарегистрировать нового пользователя."""
    result = await session.execute(
        select(User).where(User.email == data.email)
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=400,
            detail="Пользователь с таким email уже существует",
        )
    user = User(
        email=data.email,
        hashed_password=hash_password(data.password),
        role=UserRole.USER,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    status_code=200,
)
async def login(
    data: UserCreate,
    session: AsyncSession = Depends(get_async_session),
) -> TokenResponse:
    """Войти в систему и получить JWT токен."""
    result = await session.execute(
        select(User).where(User.email == data.email)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Неверный email или пароль",
        )
    from src.utils.jwt_handler import verify_password

    if not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=401,
            detail="Неверный email или пароль",
        )
    token = create_access_token(str(user.id))
    return TokenResponse(access_token=token)
