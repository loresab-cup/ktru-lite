"""Decision ownership survives guest adoption and automatic retention safely."""

from datetime import timedelta

import pytest
from sqlalchemy import select

import workers
import auth
import database as db
import decisions
import parser
import runtime as rt
from models import Decision, Item, Job, now


def remember(session, owner, code, age=0):
    job = Job(
        owner_id=owner, filename="memory.csv", status="done", created_at=now() - timedelta(days=age)
    )
    session.add(job)
    session.flush()
    item = Item(
        job_id=job.id,
        row_number=1,
        raw_text="Бумага А4",
        status="corrected",
        zone="low",
        ktru_code=code,
    )
    session.add(item)
    session.flush()
    decisions.remember(session, item)
    session.commit()
    return job, item


@pytest.fixture
def memory(api_database, monkeypatch):
    monkeypatch.setattr(rt, "AUTH_REQUIRED", True)
    monkeypatch.setattr(rt, "REMEMBER", True)


def test_retention_keeps_decisions_private_and_reusable(memory, monkeypatch):
    with db.SessionLocal() as s:
        owner = auth.create_user(s, "owner", "correct password")
        other = auth.create_user(s, "other", "correct password")
        old, _ = remember(s, owner.id, "saved-code", age=3)
        own_job = Job(owner_id=owner.id, filename="own.csv")
        other_job = Job(owner_id=other.id, filename="other.csv")
        s.add_all([own_job, other_job])
        s.commit()
    monkeypatch.setattr(rt, "KEEP_DAYS", 1)
    assert workers.clean_old_jobs() == 1
    rows = [parser.Item(1, "Бумага А4")]
    assert (
        decisions.recalled(rows, own_job.id)[decisions.text_key(rows[0].text)].ktru_code
        == "saved-code"
    )
    assert decisions.recalled(rows, other_job.id) == {}
    with db.SessionLocal() as s:
        assert s.get(Job, old.id) is None
        assert s.scalar(select(Decision.owner_id)) == owner.id


@pytest.mark.parametrize("guest_newer", [False, True])
def test_adoption_moves_memory_and_resolves_conflicts(memory, guest_newer):
    with db.SessionLocal() as s:
        token, guest = auth.start_guest(s, 2)
        owner = auth.create_user(s, "owner", "correct password")
        _, _ = remember(s, owner.id, "account-code")
        guest_job, _ = remember(s, guest.id, "guest-code")
        remembered = s.get(Decision, decisions.text_key("Бумага А4", guest.id))
        remembered.decided_at = now() + timedelta(days=1 if guest_newer else -1)
        s.commit()
        auth.adopt_guest(s, token, owner)
        assert s.get(Decision, decisions.text_key("Бумага А4", guest.id)) is None
        assert len(s.scalars(select(Decision)).all()) == 1
    result = decisions.recalled([parser.Item(1, "Бумага А4")], guest_job.id)
    assert result[decisions.text_key("Бумага А4")].ktru_code == (
        "guest-code" if guest_newer else "account-code"
    )


def test_adoption_moves_memory_and_undo_can_forget_it(memory):
    with db.SessionLocal() as s:
        token, guest = auth.start_guest(s, 2)
        owner = auth.create_user(s, "owner", "correct password")
        job, item = remember(s, guest.id, "chosen")
        auth.adopt_guest(s, token, owner)
    assert decisions.recalled([parser.Item(1, item.raw_text)], job.id)
    with db.SessionLocal() as s:
        decisions.forget(s, s.get(Item, item.id))
        s.commit()
    assert decisions.recalled([parser.Item(1, item.raw_text)], job.id) == {}
