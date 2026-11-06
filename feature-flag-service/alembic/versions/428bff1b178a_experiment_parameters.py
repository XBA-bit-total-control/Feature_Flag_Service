"""Добавить минимальную выборку и ограничения эксперимента."""

import sqlalchemy as sa
from alembic import op


revision = "428bff1b178a"
down_revision = "07035582bda0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("experiments", sa.Column("min_sample_size", sa.Integer()))
    op.create_check_constraint(
        "ck_experiments_min_sample_size", "experiments", "min_sample_size > 0"
    )
    op.create_check_constraint(
        "ck_experiments_alpha", "experiments", "alpha > 0 AND alpha < 1"
    )
    op.create_check_constraint(
        "ck_experiments_status",
        "experiments",
        "status IN ('draft', 'running', 'paused', 'completed', 'stopped')",
    )


def downgrade() -> None:
    for name in ("status", "alpha", "min_sample_size"):
        op.drop_constraint(f"ck_experiments_{name}", "experiments", type_="check")
    op.drop_column("experiments", "min_sample_size")
