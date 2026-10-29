"""New accounts start empty; every job/item operation and remembered decision is private."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import auth
import database as db
import decisions
import parser
import privacy
import runtime as rt
from models import Decision, Item, Job


@pytest.fixture
def private_site(api_database, monkeypatch):
    monkeypatch.setattr(rt, "AUTH_REQUIRED", True)
    monkeypatch.setattr(
        rt.state,
        "catalog",
        SimpleNamespace(
            get=lambda code: {
                "code": code,
                "name": "Монитор",
                "status": "ACTIVE",
                "characteristics": [],
            }
        ),
    )
    with db.SessionLocal() as session:
        alice = auth.create_user(session, "alice", "private password")
        bob = auth.create_user(session, "bob", "private password", is_admin=True)
        for user in (alice, bob):
            privacy.record(session, user.id, "account", True, privacy.VERSION)
            privacy.record(session, user.id, "files", True, privacy.VERSION)
        job = Job(owner_id=alice.id, filename="alice-only.csv", status="done", total=1, processed=1)
        legacy = Job(filename="legacy.csv", status="done")
        session.add_all([job, legacy])
        session.flush()
        item = Item(
            job_id=job.id,
            row_number=1,
            raw_text="same row",
            status="needs_review",
            confidence=80,
            ktru_code="old",
            attributes={"name": "Монитор"},
            candidates=[],
        )
        session.add(item)
        session.commit()
        return api_database, job.id, item.id, alice.id, bob.id


def login(client, name):
    response = client.post(
        "/api/auth/login",
        json={
            "login": name,
            "password": "private password",
            "privacy_version": privacy.VERSION,
            "privacy_accepted": True,
        },
    )
    assert response.status_code == 200


def test_new_account_is_empty_and_upload_uses_the_authenticated_owner(private_site):
    app, job_id, _, _, bob_id = private_site
    with TestClient(app.app) as client:
        login(client, "bob")
        response = client.get("/api/jobs")
        assert response.json() == [] and response.headers["X-Total-Count"] == "0"
        uploaded = client.post(
            "/api/jobs", files={"file": ("mine.csv", b"Monitor")}, data={"owner_id": "1"}
        )
        assert uploaded.status_code == 202
        mine = uploaded.json()["job_id"]
        listed = client.get("/api/jobs").json()
        assert [j["id"] for j in listed] == [mine]
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
        with db.SessionLocal() as session:
            assert session.get(Job, uuid.UUID(mine)).owner_id == bob_id
        client.patch(
            "/api/auth/profile",
            json={"current_password": "private password", "login": "renamed-bob"},
        ).raise_for_status()
        assert [j["id"] for j in client.get("/api/jobs").json()] == [mine]


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/api/jobs/{job}", None),
        ("GET", "/api/jobs/{job}/items", None),
        ("GET", "/api/jobs/{job}/export?format=csv", None),
        ("DELETE", "/api/jobs/{job}", None),
        ("POST", "/api/jobs/{job}/cancel", None),
        ("POST", "/api/jobs/{job}/accept", None),
        ("POST", "/api/jobs/{job}/revert", {"all_accepted": True}),
        ("GET", "/api/items/{item}", None),
        ("PATCH", "/api/items/{item}", {"ktru_code": "new"}),
        ("PUT", "/api/items/{item}/attributes", {"name": "spoof"}),
        ("POST", "/api/items/{item}/accept", None),
        ("POST", "/api/items/{item}/undo-code", None),
        ("POST", "/api/items/{item}/revert", None),
        ("POST", "/api/items/{item}/apply-same", None),
    ],
)
def test_other_account_even_admin_cannot_read_or_change_private_data(
    private_site, method, path, body
):
    app, job_id, item_id, _, _ = private_site
    with TestClient(app.app) as client:
        login(client, "bob")
        assert (
            client.request(method, path.format(job=job_id, item=item_id), json=body).status_code
            == 404
        )
    with db.SessionLocal() as session:
        assert session.get(Job, job_id).status == "done"
        item = session.get(Item, item_id)
        assert item.status == "needs_review" and item.ktru_code == "old"


def test_concurrent_requests_do_not_mix_request_owners(private_site):
    app, job_id, _, _, _ = private_site
    with TestClient(app.app) as alice, TestClient(app.app) as bob:
        login(alice, "alice")
        login(bob, "bob")

        def check(pair):
            client, expected = pair
            for _ in range(5):
                assert [j["id"] for j in client.get("/api/jobs").json()] == expected

        with ThreadPoolExecutor(2) as pool:
            list(pool.map(check, [(alice, [str(job_id)]), (bob, [])]))


def test_remembered_decisions_do_not_leak_or_overwrite_other_accounts(private_site):
    _, alice_job, alice_item, alice_id, bob_id = private_site
    with db.SessionLocal() as session:
        item = session.get(Item, alice_item)
        decisions.remember(session, item)
        bob_job = Job(owner_id=bob_id, filename="bob.csv", status="done")
        session.add(bob_job)
        session.flush()
        bob_item = Item(
            job_id=bob_job.id,
            row_number=1,
            raw_text="same row",
            status="corrected",
            ktru_code="different",
        )
        session.add(bob_item)
        session.commit()
        bob_job_id = bob_job.id
    rows = [parser.Item(1, "same row")]
    assert decisions.recalled(rows, bob_job_id) == {}
    with db.SessionLocal() as session:
        decisions.remember(session, session.get(Item, bob_item.id))
        session.commit()
        assert len(session.scalars(select(Decision)).all()) == 2
    assert decisions.recalled(rows, alice_job)[decisions.text_key("same row")].ktru_code == "old"
    assert (
        decisions.recalled(rows, bob_job_id)[decisions.text_key("same row")].ktru_code
        == "different"
    )
    assert decisions.text_key("same row", alice_id) != decisions.text_key("same row", bob_id)
