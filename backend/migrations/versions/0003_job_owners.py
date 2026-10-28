"""Keep each account's specifications private; legacy jobs remain unassigned."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_jobs_owner_id_users", "users", ["owner_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_jobs_owner_id", ["owner_id"])


def downgrade():
    with op.batch_alter_table("jobs") as batch:
        batch.drop_index("ix_jobs_owner_id")
        batch.drop_constraint("fk_jobs_owner_id_users", type_="foreignkey")
        batch.drop_column("owner_id")
