import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class ExperimentStatus(StrEnum):
    DRAFT = "draft"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    STOPPED = "stopped"


class Experiment(Base, TimestampMixin):
    """Эксперименты (A/B тесты)."""

    __tablename__ = "experiments"
    __table_args__ = (
        CheckConstraint("min_sample_size > 0", name="ck_experiments_min_sample_size"),
        CheckConstraint("alpha > 0 AND alpha < 1", name="ck_experiments_alpha"),
        CheckConstraint(
            "status IN ('draft', 'running', 'paused', 'completed', 'stopped')",
            name="ck_experiments_status",
        ),
        Index(
            "uq_experiments_active_flag", "flag_id", unique=True,
            postgresql_where=text("status IN ('running', 'paused')"),
        ),
    )

    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default=ExperimentStatus.DRAFT, nullable=False)
    primary_metric: Mapped[str] = mapped_column(String(255), nullable=False)
    alpha: Mapped[float] = mapped_column(Float, default=0.05, nullable=False)
    min_sample_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    flag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flags.id"), nullable=False)
    creator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    start_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationships
    flag: Mapped["Flag"] = relationship("Flag")
    creator: Mapped["User"] = relationship("User", back_populates="experiments")
    results: Mapped[list] = relationship("ExperimentResult", back_populates="experiment", cascade="all, delete-orphan")
    reports: Mapped[list] = relationship("ExperimentReport", back_populates="experiment", cascade="all, delete-orphan")
