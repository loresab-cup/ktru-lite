"""Schema creation and lossless, explicit legacy adoption on SQLite/PostgreSQL."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.engine import make_url

from auth import AuthBase
from migrate import configuration, upgrade_database
from models import Base
from vector_store import metadata as vector_metadata


@pytest.fixture(params=["sqlite", "postgresql"])
def database(request, tmp_path):
    if request.param == "sqlite":
        engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
        yield engine
        engine.dispose()
        return
    url = os.getenv("MIGRATION_TEST_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL migration test server not configured")
    name = "migration_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.exec_driver_sql(f"CREATE DATABASE {name}")
    engine = create_engine(make_url(url).set(database=name))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.exec_driver_sql(f"DROP DATABASE {name}")
        admin.dispose()


def test_fresh_and_repeated_upgrade_matches_models(database):
    upgrade_database(database)
    upgrade_database(database)
    with database.connect() as c:
        assert c.scalar(text("select version_num from alembic_version")) == "0005"
        assert (
            compare_metadata(
                MigrationContext.configure(c), [Base.metadata, AuthBase.metadata, vector_metadata]
            )
            == []
        )


def test_legacy_adoption_preserves_all_tables(database):
    legacy = MetaData()
    with database.begin() as c:
        command.upgrade(configuration(c), "0001")
        c.exec_driver_sql("DROP TABLE alembic_version")
        legacy.reflect(c)
        when, job_id = datetime.now(UTC), uuid.uuid4()
        if database.dialect.name == "sqlite":
            job_id = job_id.hex
        user_id = c.execute(
            legacy.tables["users"]
            .insert()
            .values(
                login="retained",
                password_hash="existing-hash",
                is_admin=False,
                created_at=when,
            )
            .returning(legacy.tables["users"].c.id)
        ).scalar_one()
        c.execute(
            legacy.tables["jobs"]
            .insert()
            .values(
                id=job_id,
                filename="retained.csv",
                status="done",
                total=1,
                processed=1,
                include_inactive=False,
                created_at=when,
                stage_seconds={},
            )
        )
        c.execute(
            legacy.tables["items"]
            .insert()
            .values(
                job_id=job_id,
                row_number=1,
                raw_text="retained",
                status="accepted",
                attributes={},
                candidates=[],
                zone="low",
                summary="",
            )
        )
        c.execute(
            legacy.tables["decisions"]
            .insert()
            .values(
                key="retained",
                job_id=job_id,
                ktru_code="retained",
                ktru_name="",
                decided_at=when,
            )
        )
        c.execute(
            legacy.tables["sessions"]
            .insert()
            .values(
                token_hash="existing-token-hash",
                user_id=user_id,
                expires_at=when + timedelta(days=1),
            )
        )

    def snapshot():
        with database.connect() as c:
            return {t.name: list(c.execute(t.select()).mappings()) for t in legacy.sorted_tables}

    before = snapshot()
    with pytest.raises(RuntimeError):
        upgrade_database(database)
    upgrade_database(database, adopt=True)
    assert snapshot() == before
    with database.connect() as c:
        assert c.scalar(text("select count(*) from jobs where owner_id is not null")) == 0
        assert c.scalar(text("select count(*) from user_consents")) == 0


def test_incompatible_legacy_is_not_stamped(database):
    with database.begin() as c:
        c.exec_driver_sql("CREATE TABLE jobs (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="differs"):
        upgrade_database(database, adopt=True)
    assert "alembic_version" not in inspect(database).get_table_names()


def test_concurrent_upgrades_are_serialized(database):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: upgrade_database(database), range(2)))
    assert "items" in inspect(database).get_table_names()


def test_downgrade_only_on_disposable_database(database):
    upgrade_database(database)
    with database.begin() as connection:
        command.downgrade(configuration(connection), "base")
    assert set(inspect(database).get_table_names()) <= {"alembic_version"}
