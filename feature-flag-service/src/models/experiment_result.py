import uuid

from sqlalchemy import Boolean, String, Float, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class ExperimentResult(Base, TimestampMixin):
    """Результаты экспериментов."""

    __tablename__ = "experiment_results"
    __table_args__ = (
        UniqueConstraint("experiment_id", "variant_name", name="uq_experiment_results_variant"),
    )

    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    variant_name: Mapped[str] = mapped_column(String(100), nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    conversion_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    z_statistic: Mapped[float | None] = mapped_column(Float, nullable=True)
    p_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_interval_lower: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_interval_upper: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_significant: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    enough_data: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    # Relationships
    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="results")
