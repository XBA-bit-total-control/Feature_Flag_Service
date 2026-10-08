import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import String, Text, DateTime, func, Enum as SQLAlchemyEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"


class User(Base, TimestampMixin):
    """Таблица пользователей системы."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(SQLAlchemyEnum(UserRole), default=UserRole.USER, nullable=False)

    # Relationships
    flags: Mapped[list] = relationship("Flag", back_populates="owner", cascade="all, delete-orphan")
    experiments: Mapped[list] = relationship("Experiment", back_populates="creator", cascade="all, delete-orphan")
