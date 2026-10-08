import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, func, Float, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class ExperimentResult(Base, TimestampMixin):
    """Результаты экспериментов."""

    __tablename__ = "experiment_results"

    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversion_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    z_statistic: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Relationships
    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="results")
