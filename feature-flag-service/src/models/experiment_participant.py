from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base


class ExperimentParticipant(Base):
    """Данные участника, необходимые для пересчёта после очистки событий."""

    __tablename__ = "experiment_participants"
    id = None

    experiment_id: Mapped[UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"), primary_key=True,
    )
    user_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    variant_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    impression_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conversion_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
