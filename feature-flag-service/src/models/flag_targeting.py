import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, func, Boolean, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class FlagTargeting(Base, TimestampMixin):
    """Правила таргетинга для feature-флагов."""

    __tablename__ = "flag_targeting_rules"

    attribute: Mapped[str] = mapped_column(String(255), nullable=False)
    operator: Mapped[str] = mapped_column(String(50), nullable=False)
    values: Mapped[list] = mapped_column(JSON, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    flag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flags.id"), nullable=False)

    # Relationships
    flag: Mapped["Flag"] = relationship("Flag", back_populates="targeting_rules")
