from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from src.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Хешировать пароль через bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Проверить пароль против хеша."""
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    """Создать JWT токен с указанным subject."""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """Раскодировать JWT токен. Возвращает subject или None."""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload.get("sub")
    except JWTError:
        return None
