"""Separate consent versions and withdrawal are enforced on the server."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import auth
import database as db
import privacy
import runtime as rt
from models import Job


@pytest.fixture
def site(api_database, monkeypatch):
    monkeypatch.setattr(rt, "AUTH_REQUIRED", True)
    with db.SessionLocal() as session:
        auth.create_user(session, "consent-user", "private password")
    return api_database


def sign_in(client):
    client.post(
        "/api/auth/login", json={"login": "consent-user", "password": "private password"}
    ).raise_for_status()


def consent(client, purpose, accepted=True, version=privacy.VERSION):
    return client.post(
        "/api/auth/consents", json={"purpose": purpose, "accepted": accepted, "version": version}
    )


def test_public_documents_metadata_and_required_registration_consent(site):
    with TestClient(site.app) as client:
        assert client.get("/api/legal").json()["version"] == privacy.VERSION
        assert client.get("/api/auth/consents").status_code == 401
        response = client.post(
            "/api/auth/register", json={"login": "new-user", "password": "long password"}
        )
        assert response.status_code == 400
        assert (
            client.post(
                "/api/auth/register",
                json={
                    "login": "new-user",
                    "password": "long password",
                    "privacy_version": privacy.VERSION,
                },
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/auth/register",
                json={
                    "login": "new-user",
                    "password": "long password",
                    "privacy_version": privacy.VERSION,
                    "privacy_accepted": True,
                },
            ).status_code
            == 201
        )
        state = client.get("/api/auth/consents").json()
        assert state["account"] and not state["files"] and not state["improvement"]


def test_upload_is_blocked_without_required_consents_and_improvement_is_optional(site):
    with TestClient(site.app) as client:
        sign_in(client)
        upload = lambda: client.post("/api/jobs", files={"file": ("mine.txt", b"Monitor")})
        assert upload().status_code == 403
        assert consent(client, "improvement").status_code == 400
        assert consent(client, "account").status_code == 200
        assert upload().status_code == 403
        assert consent(client, "files", version="old").status_code == 400
        assert consent(client, "files").status_code == 200
        assert upload().status_code == 202
        assert not client.get("/api/auth/consents").json()["improvement"]
        assert consent(client, "improvement").status_code == 200
        assert consent(client, "improvement", False).status_code == 200
        assert upload().status_code == 202


def test_withdrawal_preserves_history_and_cancels_only_the_owners_jobs(site):
    with TestClient(site.app) as client:
        sign_in(client)
        for purpose in privacy.PURPOSES:
            consent(client, purpose).raise_for_status()
        job_id = client.post("/api/jobs", files={"file": ("queued.txt", b"Monitor")}).json()[
            "job_id"
        ]
        with db.SessionLocal() as session:
            session.add(Job(filename="legacy.txt", status="queued"))
            session.commit()
        consent(client, "files", False).raise_for_status()
        state = client.get("/api/auth/consents").json()
        assert state["account"] and not state["files"] and not state["improvement"]
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"
        assert (
            client.post("/api/jobs", files={"file": ("blocked.txt", b"Monitor")}).status_code == 403
        )
    with db.SessionLocal() as session:
        assert session.scalar(select(Job).where(Job.filename == "legacy.txt")).status == "queued"
        events = session.scalars(select(privacy.Consent).order_by(privacy.Consent.id)).all()
        assert any(event.purpose == "files" and event.accepted for event in events)
        assert any(event.purpose == "files" and not event.accepted for event in events)


def test_worker_refuses_legacy_or_unconsented_queued_jobs(site):
    from job_store import cancelled, claim_job

    with db.SessionLocal() as session:
        user = session.scalar(select(auth.User).where(auth.User.login == "consent-user"))
        legacy = Job(filename="legacy.txt", status="queued")
        unconsented = Job(owner_id=user.id, filename="new.txt", status="queued")
        session.add_all([legacy, unconsented])
        session.commit()
        ids = [legacy.id, unconsented.id]
    for job_id in ids:
        assert claim_job(job_id) is None
        assert cancelled(job_id)
