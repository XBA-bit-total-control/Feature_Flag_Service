from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_async_session
from src.models.user import User
from src.utils.jwt_handler import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


async def get_db(session: AsyncSession = Depends(get_async_session)) -> AsyncSession:
    """FastAPI dependency для получения асинхронной сессии PostgreSQL."""
    return session


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    """Получить текущего пользователя из JWT токена."""
    user_id = decode_access_token(token)
    if user_id is None:
        raise HTTPException(
            status_code=401,
            detail="Недействительный токен",
        )
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Пользователь не найден",
        )
    return user
