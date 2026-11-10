from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_async_session
from src.dependencies import get_current_user, require_admin
from src.models.user import User, UserRole
from src.schemas.user import UserCreate, UserResponse, TokenResponse, UserRoleUpdate
from src.services.audit_service import record_change
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
        role=UserRole.VIEWER,
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


@router.get("/auth/me", response_model=UserResponse)
async def read_current_user(user: User = Depends(get_current_user)) -> User:
    """Получить свой профиль и роль."""
    return user


@router.patch("/auth/users/{user_id}/role", response_model=UserResponse)
async def update_user_role(
    user_id: UUID,
    data: UserRoleUpdate,
    session: AsyncSession = Depends(get_async_session),
    admin: User = Depends(require_admin),
) -> User:
    """Изменить роль пользователя, сохраняя доступ действующих администраторов."""
    user = await session.get(User, user_id, with_for_update=True, populate_existing=True)
    if user is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if user.role == UserRole.ADMIN and data.role != UserRole.ADMIN:
        raise HTTPException(status_code=409, detail="Понижение администратора через API запрещено")
    previous_role = user.role
    user.role = data.role
    record_change(session, admin.id, "user", user.id, "role_changed",
                  before={"role": previous_role}, after={"role": data.role})
    return user
