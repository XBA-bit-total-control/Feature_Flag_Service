"""Закрепить уникальность вариантов, результатов и активных экспериментов."""

from alembic import op
import sqlalchemy as sa


revision = "b1288dfcdb10"
down_revision = "7ab1260d851f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_flag_variants_flag_key", "flag_variants", ["flag_id", "variant_key"])
    op.create_unique_constraint(
        "uq_experiment_results_variant", "experiment_results", ["experiment_id", "variant_name"],
    )
    op.create_index(
        "uq_experiments_active_flag", "experiments", ["flag_id"], unique=True,
        postgresql_where=sa.text("status IN ('running', 'paused')"),
    )


def downgrade() -> None:
    op.drop_index("uq_experiments_active_flag", table_name="experiments")
    op.drop_constraint("uq_experiment_results_variant", "experiment_results", type_="unique")
    op.drop_constraint("uq_flag_variants_flag_key", "flag_variants", type_="unique")
