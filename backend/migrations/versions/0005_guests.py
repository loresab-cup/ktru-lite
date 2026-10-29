"""Guests: visitors who work without an account until the browser is closed."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column("is_guest", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_guest")
