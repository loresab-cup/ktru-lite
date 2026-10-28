"""Accounts: password hashes, login and logout, closed API, wrong passwords, the first admin."""

import importlib
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import auth


@pytest.fixture
def site(monkeypatch, tmp_path):
    """The API with accounts on, its own SQLite database and a user «ivan»."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'initial.db'}")
    api = importlib.import_module("api")
    engine = api.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    api.Base.metadata.create_all(engine)
    auth.AuthBase.metadata.create_all(engine)
    sessions = api.sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(api, "SessionLocal", sessions)
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "throttle", auth.Throttle())

    @asynccontextmanager
    async def lifespan(_):
        yield

    monkeypatch.setattr(api.app.router, "lifespan_context", lifespan)
    with sessions() as session:
        auth.create_user(session, "Ivan", "correct horse")
    yield api
    engine.dispose()


def log_in(client, login="ivan", password="correct horse"):
    return client.post("/api/auth/login", json={"login": login, "password": password})


def test_password_hash_is_salted_and_checked():
    first, second = auth.hash_password("пароль-123"), auth.hash_password("пароль-123")
    assert first != second and first.startswith("scrypt$")
    assert auth.verify_password("пароль-123", first)
    assert not auth.verify_password("пароль-124", first)
    assert not auth.verify_password("x", "garbage")


def test_api_is_closed_without_a_login_and_open_after(site):
    with TestClient(site.app) as client:
        assert client.get("/api/jobs").status_code == 401
        assert client.get("/api/auth/me").status_code == 401
        assert client.get("/api/health").json()["auth"] == "on"  # health stays public
        answer = log_in(client, login=" IVAN ")  # case and spaces of the login do not matter
        assert answer.status_code == 200 and answer.json() == {"login": "ivan", "is_admin": False}
        cookie = answer.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=lax" in cookie
        assert client.get("/api/jobs").status_code == 200
        assert client.get("/api/auth/me").json()["login"] == "ivan"
        assert client.post("/api/auth/logout").status_code == 204
        assert client.get("/api/jobs").status_code == 401


def test_the_database_keeps_no_token(site):
    with TestClient(site.app) as client:
        token = log_in(client).cookies[site.COOKIE]
    with site.SessionLocal() as session:
        stored = session.scalars(select(auth.LoginSession.token_hash)).all()
    assert len(stored) == 1 and token not in stored[0]


def test_wrong_password_and_unknown_login_answer_the_same(site):
    with TestClient(site.app) as client:
        wrong = log_in(client, password="wrong password")
        unknown = log_in(client, login="nobody")
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()


def test_many_wrong_passwords_block_the_login_for_a_while(site):
    with TestClient(site.app) as client:
        for _ in range(auth.MAX_FAILS):
            assert log_in(client, password="wrong password").status_code == 401
        assert log_in(client).status_code == 429  # even the right password waits
        assert log_in(client, login="other").status_code == 401  # another login is not blocked


def test_an_expired_session_does_not_open_the_api(site, monkeypatch):
    monkeypatch.setattr(site, "SESSION_HOURS", -1.0)
    with TestClient(site.app) as client:
        assert log_in(client).status_code == 200
        assert client.get("/api/jobs").status_code == 401


def test_a_new_password_ends_the_sessions(site):
    with TestClient(site.app) as client:
        log_in(client)
        with site.SessionLocal() as session:
            auth.set_password(session, "ivan", "another horse")
        assert client.get("/api/jobs").status_code == 401
        assert log_in(client, password="another horse").status_code == 200


def test_first_admin_only_when_there_are_no_users(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    auth.AuthBase.metadata.create_all(engine)
    with sessionmaker(engine)() as session:
        assert not auth.ensure_admin(session, "admin", "")  # no password: nobody is created
        assert not auth.has_users(session)
        assert auth.ensure_admin(session, "admin", "long enough")
        assert not auth.ensure_admin(session, "admin2", "long enough")  # only the first one
        with pytest.raises(ValueError):
            auth.create_user(session, "admin", "long enough")  # the same login twice
        with pytest.raises(ValueError):
            auth.create_user(session, "петя", "long enough")  # not Latin
        with pytest.raises(ValueError):
            auth.create_user(session, "petya", "short")
    engine.dispose()


def test_health_says_when_nobody_can_log_in(site):
    with site.SessionLocal() as session:
        auth.remove_user(session, "ivan")
    with TestClient(site.app) as client:
        assert client.get("/api/health").json()["auth"] == "no_users"
