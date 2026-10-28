"""Store catalog vectors in PostgreSQL pgvector."""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision = "0002"
down_revision = "0001"
branch_labels = depends_on = None


def upgrade():
    postgres = op.get_bind().dialect.name == "postgresql"
    if postgres:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "catalog_vectors",
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("embedding", Vector(256) if postgres else sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("catalog_vectors")
