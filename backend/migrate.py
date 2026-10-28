"""Schema upgrades; legacy adoption is explicit and verified before stamping."""

import argparse
import os
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import DateTime, MetaData, create_engine, inspect

ROOT = Path(__file__).resolve().parent
INITIAL = "0001"


def baseline_metadata():
    """Reflect the frozen initial revision, never future ORM definitions."""
    reference = create_engine("sqlite://")
    try:
        with reference.begin() as connection:
            command.upgrade(configuration(connection), INITIAL)
            metadata = MetaData()
            metadata.reflect(connection, only=["jobs", "items", "decisions", "users", "sessions"])
            # SQLite reflection loses timezone=True; baseline timestamps use UTC.
            for table in metadata.tables.values():
                for column in table.columns:
                    if isinstance(column.type, DateTime):
                        column.type = DateTime(timezone=True)
            return metadata
    finally:
        reference.dispose()


def compare_type(context, actual, expected, actual_type, expected_type):
    uuid_columns = {("jobs", "id"), ("items", "job_id"), ("decisions", "job_id")}
    if (
        (actual.table.name, actual.name) in uuid_columns
        and str(actual_type).upper() == "UUID"
        and str(expected_type).upper() == "CHAR(32)"
    ):
        return False
    return None


def configuration(connection):
    config = Config()
    config.set_main_option("script_location", str(ROOT / "migrations"))
    config.attributes["connection"] = connection
    return config


def upgrade_database(engine, adopt=False):
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.exec_driver_sql("SELECT pg_advisory_xact_lock(684291735)")
        elif engine.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        tables = set(inspect(connection).get_table_names())
        config = configuration(connection)
        if tables and "alembic_version" not in tables:
            if not adopt:
                raise RuntimeError(
                    "Unversioned database: back up and run python migrate.py --adopt-legacy"
                )
            expected = baseline_metadata()
            inspector = inspect(connection)
            for table in expected.tables.values():
                if table.name not in tables or inspector.get_pk_constraint(table.name)[
                    "constrained_columns"
                ] != [c.name for c in table.primary_key.columns]:
                    raise RuntimeError("Legacy primary key differs from baseline; adoption refused")
            differences = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": compare_type}),
                expected,
            )
            if differences:
                raise RuntimeError("Legacy schema differs from baseline; adoption refused")
            command.stamp(config, INITIAL)
        command.upgrade(config, "head")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adopt-legacy", action="store_true")
    args = parser.parse_args()
    upgrade_database(create_engine(os.environ["DATABASE_URL"]), adopt=args.adopt_legacy)
