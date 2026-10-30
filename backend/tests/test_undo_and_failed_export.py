"""Undo of an accept keeps a code chosen by hand; a failed job still exports its finished rows."""

import csv
import io
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

import database as db
import runtime as rt
from models import Decision, Item, Job

SYSTEM, HUMAN = "11.11.11.111-00000001", "22.22.22.222-00000002"


CODES = {
    SYSTEM: {"code": SYSTEM, "name": "Код системы", "status": "ACTIVE"},
    HUMAN: {"code": HUMAN, "name": "Код человека", "status": "ACTIVE"},
}


class FakeCatalog:
    def get(self, code):
        return CODES.get(code)


def add_item(status="auto", confidence=90.0, job_status="done") -> int:
    with db.SessionLocal() as session:
        job = Job(id=uuid.uuid4(), filename="spec.csv", status=job_status, total=2, processed=1)
        item = Item(
            job_id=job.id,
            row_number=1,
            raw_text="Монитор 24 дюйма",
            status=status,
            ktru_code=SYSTEM,
            ktru_name="Код системы",
            confidence=confidence,
            zone="high" if confidence >= 85 else "low",
            summary="Предложение системы",
            attributes={"name": "Монитор"},
            candidates=[{"code": SYSTEM, "name": "Код системы", "score": 0.9}],
        )
        session.add_all([job, item])
        session.commit()
        return item.id


def decisions() -> list[str]:
    with db.SessionLocal() as session:
        return list(session.scalars(select(Decision.ktru_code)))


def test_undo_of_accept_keeps_the_code_chosen_by_hand(api_database, monkeypatch):
    monkeypatch.setattr(rt.state, "catalog", FakeCatalog())
    item_id = add_item()
    with TestClient(api_database.app) as client:
        assert client.patch(f"/api/items/{item_id}", json={"ktru_code": HUMAN}).status_code == 200
        assert client.post(f"/api/items/{item_id}/accept").json()["status"] == "accepted"
        undone = client.post(f"/api/items/{item_id}/unaccept").json()
        assert (undone["ktru_code"], undone["status"]) == (HUMAN, "corrected")
        assert undone["confidence"] is None  # a person's choice keeps no made-up percentage
        assert decisions() == [HUMAN]  # the manual decision is still remembered
        # the accept is undone once; a second click has nothing to undo
        assert client.post(f"/api/items/{item_id}/unaccept").status_code == 400
        # "Вернуть на проверку" still brings back the proposal of the system
        reverted = client.post(f"/api/items/{item_id}/revert").json()
        assert reverted["ktru_code"] == SYSTEM and decisions() == []


def test_undo_of_accept_of_a_doubtful_row_forgets_the_acceptance(api_database, monkeypatch):
    monkeypatch.setattr(rt.state, "catalog", FakeCatalog())
    item_id = add_item(status="needs_review", confidence=55.0)
    with TestClient(api_database.app) as client:
        client.post(f"/api/items/{item_id}/accept")
        assert decisions() == [SYSTEM]  # a person agreed with a doubtful row: remembered
        undone = client.post(f"/api/items/{item_id}/unaccept").json()
    assert (undone["status"], undone["confidence"], undone["zone"]) == ("needs_review", 55.0, "low")
    assert decisions() == []


def test_a_failed_job_exports_the_rows_finished_before_the_failure(api_database):
    add_item(job_status="failed")
    with db.SessionLocal() as session:
        job_id = session.scalar(select(Job.id))
    with TestClient(api_database.app) as client:
        answer = client.get(f"/api/jobs/{job_id}/export", params={"format": "csv"})
        assert answer.status_code == 200
        rows = list(csv.reader(io.StringIO(answer.content.decode("utf-8-sig")), delimiter=";"))
        assert len(rows) == 2 and SYSTEM in rows[1]
        queued = Job(id=uuid.uuid4(), filename="wait.csv", status="queued")
        with db.SessionLocal() as session:
            session.add(queued)
            session.commit()
        assert client.get(f"/api/jobs/{queued.id}/export").status_code == 409
