"""Accounts: password hashes, registration, login and logout, closed API, wrong passwords."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import auth


@pytest.fixture
def site(api_database, monkeypatch):
    """Use the shared isolated database with real account checks enabled."""
    monkeypatch.setattr(runtime, "AUTH_REQUIRED", True)
    monkeypatch.setattr(routes.accounts, "throttle", auth.Throttle())
    monkeypatch.setattr(routes.accounts, "registrations", auth.Throttle())
    monkeypatch.setattr(routes.accounts, "profile_attempts", auth.Throttle())
    with database.SessionLocal() as session:
        auth.create_user(session, "Ivan", "correct horse")
    return api_database


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
        token = log_in(client).cookies[runtime.COOKIE]
    with database.SessionLocal() as session:
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
    monkeypatch.setattr(runtime, "SESSION_HOURS", -1.0)
    with TestClient(site.app) as client:
        assert log_in(client).status_code == 200
        assert client.get("/api/jobs").status_code == 401


def test_a_new_password_ends_the_sessions(site):
    with TestClient(site.app) as client:
        log_in(client)
        with database.SessionLocal() as session:
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


def register(client, login="petya", password="long enough"):
    import privacy

    return client.post(
        "/api/auth/register",
        json={
            "login": login,
            "password": password,
            "privacy_version": privacy.VERSION,
            "privacy_accepted": True,
        },
    )


def test_anyone_registers_and_is_logged_in_at_once(site):
    with TestClient(site.app) as client:
        assert client.get("/api/health").json()["auth"] == "on"
        answer = register(client, login=" Petya ")
        assert answer.status_code == 201 and answer.json() == {"login": "petya", "is_admin": False}
        assert "httponly" in answer.headers["set-cookie"].lower()
        assert client.get("/api/jobs").status_code == 200
        client.post("/api/auth/logout")
        assert log_in(client, login="petya", password="long enough").status_code == 200


def test_registration_says_what_is_wrong(site):
    with TestClient(site.app) as client:
        taken = register(client, login="IVAN")
        assert taken.status_code == 409 and "занят" in taken.json()["detail"]
        assert register(client, login="петя").status_code == 400  # not Latin
        assert register(client, login="ab").status_code == 400  # too short
        short = register(client, password="short")
        assert short.status_code == 400 and "Пароль" in short.json()["detail"]
        assert client.get("/api/jobs").status_code == 401  # nobody got logged in


def test_many_accounts_from_one_address_wait(site):
    with TestClient(site.app) as client:
        for n in range(auth.MAX_FAILS):
            assert register(client, login=f"user{n}").status_code == 201
        assert register(client, login="one-more").status_code == 429


def test_profile_login_change_rotates_sessions_and_preserves_the_account(site):
    with TestClient(site.app) as client, TestClient(site.app) as other:
        log_in(client)
        log_in(other)
        old = client.cookies[runtime.COOKIE]
        response = client.patch(
            "/api/auth/profile", json={"current_password": "correct horse", "login": " New.Login "}
        )
        assert response.status_code == 200 and response.json() == {
            "login": "new.login",
            "is_admin": False,
        }
        assert client.cookies[runtime.COOKIE] != old
        assert client.get("/api/auth/me").json()["login"] == "new.login"
        assert other.get("/api/auth/me").status_code == 401
        assert log_in(other).status_code == 401
        assert log_in(other, login="new.login").status_code == 200
        with database.SessionLocal() as session:
            users = session.scalars(select(auth.User)).all()
            assert len(users) == 1 and users[0].id == 1


def test_profile_password_change_rejects_the_old_password(site):
    with TestClient(site.app) as client, TestClient(site.app) as other:
        log_in(client)
        log_in(other)
        response = client.patch(
            "/api/auth/profile",
            json={"current_password": "correct horse", "password": "new correct horse"},
        )
        assert response.status_code == 200
        assert client.get("/api/auth/me").status_code == 200
        assert other.get("/api/auth/me").status_code == 401
        assert log_in(other).status_code == 401
        assert log_in(other, password="new correct horse").status_code == 200
        assert set(response.json()) == {"login", "is_admin"}


def test_profile_wrong_password_and_taken_login_do_not_change_anything(site):
    with database.SessionLocal() as session:
        auth.create_user(session, "taken", "a different password")
    with TestClient(site.app) as client:
        log_in(client)
        cookie = client.cookies[runtime.COOKIE]
        response = client.patch(
            "/api/auth/profile", json={"current_password": "wrong", "login": "new-login"}
        )
        assert response.status_code == 403
        response = client.patch(
            "/api/auth/profile",
            json={
                "current_password": "correct horse",
                "login": "TAKEN",
                "password": "new password",
            },
        )
        assert response.status_code == 409
        assert client.cookies[runtime.COOKIE] == cookie
        assert client.get("/api/auth/me").json()["login"] == "ivan"
        assert log_in(client).status_code == 200


@pytest.mark.parametrize("changes", [{}, {"login": "абв"}, {"password": "short"}])
def test_profile_validates_changes_without_ending_the_session(site, changes):
    with TestClient(site.app) as client:
        log_in(client)
        response = client.patch(
            "/api/auth/profile", json={"current_password": "correct horse", **changes}
        )
        assert response.status_code == 400
        assert client.get("/api/auth/me").json()["login"] == "ivan"


def test_profile_cannot_change_another_account_or_grant_admin(site):
    with TestClient(site.app) as client:
        assert (
            client.patch(
                "/api/auth/profile",
                json={"current_password": "correct horse", "login": "new-login"},
            ).status_code
            == 401
        )
        log_in(client)
        response = client.patch(
            "/api/auth/profile",
            json={"current_password": "correct horse", "is_admin": True, "user_id": 99},
        )
        assert response.status_code == 422
        assert client.get("/api/auth/me").json() == {"login": "ivan", "is_admin": False}


def test_profile_wrong_password_is_throttled(site):
    with TestClient(site.app) as client:
        log_in(client)
        for _ in range(auth.MAX_FAILS):
            assert (
                client.patch(
                    "/api/auth/profile", json={"current_password": "wrong", "login": "new-login"}
                ).status_code
                == 403
            )
        assert (
            client.patch(
                "/api/auth/profile",
                json={"current_password": "correct horse", "login": "new-login"},
            ).status_code
            == 429
        )


def test_open_development_mode_does_not_authorize_profile_changes(site, monkeypatch):
    monkeypatch.setattr(runtime, "AUTH_REQUIRED", False)
    with TestClient(site.app) as client:
        assert (
            client.patch(
                "/api/auth/profile",
                json={"current_password": "correct horse", "login": "new-login"},
            ).status_code
            == 401
        )


import database
import routes
import routes.accounts
import runtime
