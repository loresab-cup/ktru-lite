"""The jury demo preserves measured predictions and stays isolated to its own account."""

import gzip
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

import auth
import database as db
import demo
import privacy
import runtime as rt
from models import Item, Job


def test_demo_snapshot_rejects_changed_accuracy(tmp_path):
    data = demo.load_snapshot()
    assert data["exact"] == 991 and len(data["items"]) == 1000
    data["items"][0]["ktru_code"] = "changed"
    path = tmp_path / "changed.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as target:
        json.dump(data, target)
    with pytest.raises(ValueError, match="991/1000"):
        demo.load_snapshot(path)


def test_demo_is_idempotent_and_preserves_user_edits(api_database, tmp_path):
    data = demo.load_snapshot()
    with db.SessionLocal() as session:
        job_id, created = demo.seed_demo(session, data, tmp_path)
        assert created
        rows = session.scalars(select(Item).where(Item.job_id == job_id)).all()
        assert len(rows) == 1000
        assert sum(r.ktru_code == data["reference_codes"][str(r.row_number)] for r in rows) == 991
        assert auth.verify_password("admin", session.scalar(select(auth.User)).password_hash)
        rows[0].status = "accepted"
        session.commit()
        assert demo.seed_demo(session, data, tmp_path) == (job_id, False)
        assert session.scalar(select(func.count()).select_from(Job)) == 1
        assert session.get(Item, rows[0].id).status == "accepted"
    assert (tmp_path / f"{job_id}.xlsx").read_bytes().startswith(b"PK")
    with pytest.raises(ValueError, match="8"):
        auth.check_password("admin")


def test_demo_never_overwrites_existing_account(api_database, tmp_path):
    with db.SessionLocal() as session:
        user = auth.create_user(session, "admin", "existing-password", is_admin=True)
        original = user.password_hash
        with pytest.raises(ValueError, match="уже существует"):
            demo.seed_demo(session, demo.load_snapshot(), tmp_path)
        assert session.get(auth.User, user.id).password_hash == original
        assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_demo_visible_to_admin_but_not_another_user(api_database, tmp_path, monkeypatch):
    monkeypatch.setattr(rt, "AUTH_REQUIRED", True)
    with db.SessionLocal() as session:
        job_id, _ = demo.seed_demo(session, demo.load_snapshot(), tmp_path)
        auth.create_user(session, "other-user", "other-password")
    policy = {"privacy_version": privacy.VERSION, "privacy_accepted": True}
    with TestClient(api_database.app) as client:
        login = client.post(
            "/api/auth/login", json=policy | {"login": "admin", "password": "admin"}
        )
        assert login.status_code == 200
        jobs = client.get("/api/jobs").json()
        assert any(job["id"] == str(job_id) and job["total"] == 1000 for job in jobs)
        assert client.get(f"/api/jobs/{job_id}/items").json()["total"] == 1000
    with TestClient(api_database.app) as client:
        login = client.post(
            "/api/auth/login", json=policy | {"login": "other-user", "password": "other-password"}
        )
        assert login.status_code == 200 and client.get("/api/jobs").json() == []
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
