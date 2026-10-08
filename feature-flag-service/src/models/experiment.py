import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, func, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class Experiment(Base, TimestampMixin):
    """Эксперименты (A/B тесты)."""

    __tablename__ = "experiments"

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="draft", nullable=False)
    primary_metric: Mapped[str] = mapped_column(String(255), nullable=False)
    alpha: Mapped[float] = mapped_column(Float, default=0.05, nullable=False)
    flag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flags.id"), nullable=False)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    flag: Mapped["Flag"] = relationship("Flag")
    creator: Mapped["User"] = relationship("User", back_populates="experiments")
    results: Mapped[list] = relationship("ExperimentResult", back_populates="experiment", cascade="all, delete-orphan")
    reports: Mapped[list] = relationship("ExperimentReport", back_populates="experiment", cascade="all, delete-orphan")
