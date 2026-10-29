"""Guests: work without an account after the privacy policy; files live while the browser is
open, go with the account on login or registration, and are removed when the session is over."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

import auth
import database as db
import job_store
import privacy
import routes.accounts
import runtime as rt
import workers
from models import Item, Job, now


@pytest.fixture
def site(api_database, monkeypatch):
    monkeypatch.setattr(rt, "AUTH_REQUIRED", True)
    monkeypatch.setattr(routes.accounts, "guests", auth.Throttle(limit=30))
    monkeypatch.setattr(routes.accounts, "registrations", auth.Throttle())
    return api_database


def enter_as_guest(client):
    return client.post(
        "/api/auth/guest", json={"privacy_version": privacy.VERSION, "privacy_accepted": True}
    )


def guest_upload(client) -> str:
    """The files consent as the site asks it before the first upload, then one file."""
    accepted = client.post(
        "/api/auth/consents",
        json={"purpose": "files", "accepted": True, "version": privacy.VERSION},
    )
    assert accepted.status_code == 200
    uploaded = client.post("/api/jobs", files={"file": ("guest.csv", b"Monitor")})
    assert uploaded.status_code == 202
    return uploaded.json()["job_id"]


def test_a_guest_must_accept_the_policy_and_gets_a_cookie_of_the_browser_session(site):
    with TestClient(site.app) as client:
        assert client.post("/api/auth/guest", json={}).status_code == 400
        old = {"privacy_version": "2020-01-01", "privacy_accepted": True}
        assert client.post("/api/auth/guest", json=old).status_code == 400
        assert client.get("/api/jobs").status_code == 401

        answer = enter_as_guest(client)
        assert answer.status_code == 201
        me = answer.json()
        assert me["is_guest"] and me["login"].startswith(auth.GUEST_PREFIX)
        cookie = answer.headers["set-cookie"].lower()
        assert "httponly" in cookie and "max-age" not in cookie and "expires" not in cookie
        assert client.get("/api/auth/me").json()["is_guest"] is True
        assert client.get("/api/jobs").json() == []
        consents = client.get("/api/auth/consents").json()
        assert consents["account"] and consents["files"] and not consents["improvement"]


def test_nobody_can_log_in_or_change_a_profile_as_a_guest(site):
    with TestClient(site.app) as client:
        login = enter_as_guest(client).json()["login"]
        changed = client.patch(
            "/api/auth/profile", json={"current_password": "anything", "login": "mine"}
        )
        assert changed.status_code == 403
    with TestClient(site.app) as other:
        answer = other.post(
            "/api/auth/login",
            json={
                "login": login,
                "password": "anything",
                "privacy_version": privacy.VERSION,
                "privacy_accepted": True,
            },
        )
        assert answer.status_code == 401


def test_guests_do_not_see_each_others_files(site):
    with TestClient(site.app) as first, TestClient(site.app) as second:
        enter_as_guest(first)
        enter_as_guest(second)
        job_id = guest_upload(first)
        assert [j["id"] for j in first.get("/api/jobs").json()] == [job_id]
        assert second.get("/api/jobs").json() == []
        assert second.get(f"/api/jobs/{job_id}").status_code == 404


@pytest.mark.parametrize("way", ["register", "login"])
def test_a_guest_who_logs_in_keeps_the_files_in_the_account(site, way):
    with db.SessionLocal() as session:
        auth.create_user(session, "ivan", "correct horse")
    with TestClient(site.app) as client:
        guest_login = enter_as_guest(client).json()["login"]
        job_id = guest_upload(client)
        if way == "register":
            answer = client.post(
                "/api/auth/register",
                json={
                    "login": "petya",
                    "password": "correct horse",
                    "privacy_version": privacy.VERSION,
                    "privacy_accepted": True,
                },
            )
        else:
            answer = client.post(
                "/api/auth/login",
                json={
                    "login": "ivan",
                    "password": "correct horse",
                    "privacy_version": privacy.VERSION,
                    "privacy_accepted": True,
                },
            )
        assert answer.status_code in (200, 201) and not answer.json()["is_guest"]
        assert "max-age" in answer.headers["set-cookie"].lower()
        assert [j["id"] for j in client.get("/api/jobs").json()] == [job_id]
    with db.SessionLocal() as session:
        assert session.scalar(select(auth.User).where(auth.User.login == guest_login)) is None


def test_the_worker_removes_guests_whose_session_is_over(site):
    with TestClient(site.app) as left, TestClient(site.app) as stays:
        left_login = enter_as_guest(left).json()["login"]
        left_job = guest_upload(left)
        assert left.post("/api/auth/logout").status_code == 204
        stays_login = enter_as_guest(stays).json()["login"]
        stays_job = guest_upload(stays)
    with db.SessionLocal() as session:
        job = session.scalar(select(Job).where(Job.filename == "guest.csv"))
        session.add(Item(job_id=job.id, row_number=1, raw_text="Monitor", status="auto"))
        session.commit()
        upload = job_store.upload_path(session.get(Job, job.id))
    upload.write_bytes(b"Monitor")

    assert workers.clean_guests() == 1
    with db.SessionLocal() as session:
        logins = set(session.scalars(select(auth.User.login)))
        jobs = {str(j) for j in session.scalars(select(Job.id))}
    assert left_login not in logins and stays_login in logins
    assert jobs == {stays_job} and left_job not in jobs
    assert workers.clean_guests() == 0


def test_a_guest_whose_job_is_processing_waits_for_the_next_run(site):
    with TestClient(site.app) as client:
        login = enter_as_guest(client).json()["login"]
        job_id = guest_upload(client)
    with db.SessionLocal() as session:
        session.execute(update(Job).values(status="processing"))
        session.execute(update(auth.LoginSession).values(expires_at=now() - timedelta(hours=1)))
        session.commit()
    assert workers.clean_guests() == 0
    with db.SessionLocal() as session:
        session.execute(update(Job).values(status="done"))
        session.commit()
    assert workers.clean_guests() == 1
    with db.SessionLocal() as session:
        assert session.scalar(select(auth.User).where(auth.User.login == login)) is None
        assert session.scalar(select(Job.id)) is None, job_id
