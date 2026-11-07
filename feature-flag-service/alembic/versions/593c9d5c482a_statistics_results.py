"""Добавить доверительные интервалы и признаки достаточности данных."""

import sqlalchemy as sa
from alembic import op

revision = "593c9d5c482a"
down_revision = "428bff1b178a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("experiment_results", sa.Column("confidence_interval_lower", sa.Float()))
    op.add_column("experiment_results", sa.Column("confidence_interval_upper", sa.Float()))
    for name in ("is_significant", "enough_data"):
        op.add_column("experiment_results", sa.Column(
            name, sa.Boolean(), nullable=False, server_default=sa.false()
        ))


def downgrade() -> None:
    for name in (
        "enough_data", "is_significant",
        "confidence_interval_upper", "confidence_interval_lower",
    ):
        op.drop_column("experiment_results", name)
