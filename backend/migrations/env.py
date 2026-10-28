import os

from alembic import context
from sqlalchemy import create_engine, pool

from models import Base
from privacy import Consent
from vector_store import metadata as vector_metadata

config = context.config
target_metadata = [Base.metadata, Consent.metadata, vector_metadata]


def run(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


if config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    engine = create_engine(os.environ["DATABASE_URL"], poolclass=pool.NullPool)
    with engine.connect() as connection:
        run(connection)
