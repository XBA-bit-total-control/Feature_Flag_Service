"""Разделить роли и добавить мягкое удаление флагов."""

import sqlalchemy as sa
from alembic import op


revision = "39ed1ac87f44"
down_revision = "b1288dfcdb10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE userrole RENAME VALUE 'USER' TO 'DEVELOPER'")
    op.execute("ALTER TYPE userrole ADD VALUE 'VIEWER'")
    op.add_column("flags", sa.Column("deleted_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM flags WHERE deleted_at IS NOT NULL)
               OR EXISTS (SELECT 1 FROM users WHERE role::text = 'VIEWER') THEN
                RAISE EXCEPTION 'Downgrade would restore archived flags or grant viewers write access';
            END IF;
        END $$
    """)
    op.execute("ALTER TYPE userrole RENAME TO userrole_new")
    op.execute("CREATE TYPE userrole AS ENUM ('USER', 'ADMIN')")
    op.execute("ALTER TABLE users ALTER COLUMN role TYPE userrole USING "
               "(CASE WHEN role::text = 'ADMIN' THEN 'ADMIN' ELSE 'USER' END)::userrole")
    op.execute("DROP TYPE userrole_new")
    op.drop_column("flags", "deleted_at")
