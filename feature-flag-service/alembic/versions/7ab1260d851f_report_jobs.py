"""Сохранить снимок метрик и метаданные генерации отчёта."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "7ab1260d851f"
down_revision = "593c9d5c482a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("experiment_reports", sa.Column("input_snapshot", postgresql.JSONB()))
    op.add_column("experiment_reports", sa.Column("tokens_used", sa.Integer()))
    op.add_column("experiment_reports", sa.Column("fallback_reason", sa.String(100)))


def downgrade() -> None:
    for name in ("fallback_reason", "tokens_used", "input_snapshot"):
        op.drop_column("experiment_reports", name)
