"""Concurrent changes cannot reuse a revoked session on the production database engine."""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

import auth


def test_concurrent_profile_change_uses_a_session_only_once():
    url = os.getenv("MIGRATION_TEST_URL")
    if not url:
        pytest.skip("Dedicated PostgreSQL test server not configured")
    name = "profile_" + uuid.uuid4().hex
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.exec_driver_sql(f"CREATE DATABASE {name}")
    engine = create_engine(make_url(url).set(database=name))
    try:
        auth.AuthBase.metadata.create_all(engine)
        with Session(engine) as session:
            user = auth.create_user(session, "profile-user", "initial password")
            token = auth.start_session(session, user, 1)

        def change(login):
            with Session(engine, expire_on_commit=False) as session:
                return auth.change_profile(session, token, "initial password", login, None, 1)

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(change, ["first-login", "second-login"]))
        assert sum(result is not None for result in results) == 1
        fresh, changed = next(result for result in results if result is not None)
        with Session(engine) as session:
            assert auth.user_by_token(session, token) is None
            assert auth.user_by_token(session, fresh).login == changed.login
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f"DROP DATABASE {name}")
        admin.dispose()
