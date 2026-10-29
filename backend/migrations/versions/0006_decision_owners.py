"""Keep remembered decisions independent from retained specification jobs."""

import hashlib

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("decisions") as batch:
        batch.add_column(sa.Column("owner_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("raw_text", sa.Text(), nullable=False, server_default=""))
        batch.create_foreign_key(
            "fk_decisions_owner_id_users", "users", ["owner_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_index("ix_decisions_owner_id", ["owner_id"])

    connection = op.get_bind()
    metadata = sa.MetaData()
    decisions = sa.Table("decisions", metadata, autoload_with=connection)
    jobs = sa.Table("jobs", metadata, autoload_with=connection)
    items = sa.Table("items", metadata, autoload_with=connection)
    rows = connection.execute(
        sa.select(decisions.c.key, decisions.c.job_id, jobs.c.owner_id).select_from(
            decisions.join(jobs, decisions.c.job_id == jobs.c.id)
        )
    ).all()
    for row in rows:
        for raw in connection.scalars(
            sa.select(items.c.raw_text).where(items.c.job_id == row.job_id)
        ):
            normalized = " ".join(raw.lower().replace("ё", "е").split())
            scoped = f"user:{row.owner_id}:{normalized}" if row.owner_id is not None else normalized
            if hashlib.sha256(scoped.encode()).hexdigest() == row.key:
                connection.execute(
                    decisions.update()
                    .where(decisions.c.key == row.key)
                    .values(owner_id=row.owner_id, raw_text=raw)
                )
                break
    # Missing source rows or unknown legacy hashes remain unassigned, never guessed.


def downgrade():
    with op.batch_alter_table("decisions") as batch:
        batch.drop_index("ix_decisions_owner_id")
        batch.drop_constraint("fk_decisions_owner_id_users", type_="foreignkey")
        batch.drop_column("raw_text")
        batch.drop_column("owner_id")
