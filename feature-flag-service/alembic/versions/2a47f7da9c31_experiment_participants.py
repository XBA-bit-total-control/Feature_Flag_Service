"""Сохранить вклад участников перед очисткой событий."""

import sqlalchemy as sa
from alembic import op


revision = "2a47f7da9c31"
down_revision = "39ed1ac87f44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "experiment_participants",
        sa.Column("experiment_id", sa.Uuid(), sa.ForeignKey("experiments.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("user_id", sa.String(255), primary_key=True),
        sa.Column("variant_key", sa.String(100), primary_key=True),
        sa.Column("impression_at", sa.DateTime(timezone=True)),
        sa.Column("conversion_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM experiment_participants) THEN
                RAISE EXCEPTION 'Downgrade would lose statistics for compacted events';
            END IF;
        END $$
    """)
    op.drop_table("experiment_participants")
