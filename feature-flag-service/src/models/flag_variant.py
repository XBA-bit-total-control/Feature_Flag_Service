import uuid
from datetime import datetime

from sqlalchemy import String, Text, DateTime, func, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class FlagVariant(Base, TimestampMixin):
    """Варианты A/B тестов для feature-флагов."""

    __tablename__ = "flag_variants"

    variant_key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    flag_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("flags.id"), nullable=False)

    # Relationships
    flag: Mapped["Flag"] = relationship("Flag", back_populates="variants")
