import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, func, Boolean, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class Flag(Base, TimestampMixin):
    """Feature-флаги для управления функциональностью."""

    __tablename__ = "flags"

    key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(50), default="simple", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rollout_percentage: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    salt: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)

    # Relationships
    owner: Mapped["User"] = relationship("User", back_populates="flags")
    variants: Mapped[list] = relationship("FlagVariant", back_populates="flag", cascade="all, delete-orphan")
    targeting_rules: Mapped[list] = relationship("FlagTargeting", back_populates="flag", cascade="all, delete-orphan")
