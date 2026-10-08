import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class ExperimentReport(Base, TimestampMixin):
    """Отчёты по экспериментам (LLM или шаблонные)."""

    __tablename__ = "experiment_reports"

    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(50), default="template", nullable=False)
    report_content: Mapped[str] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Relationships
    experiment: Mapped["Experiment"] = relationship("Experiment", back_populates="reports")
