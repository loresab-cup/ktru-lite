"""Second opinion from the top-5: only candidate codes, only doubtful rows, user edits are kept."""

import importlib
import json
import time
import uuid

import pytest
from fastapi.testclient import TestClient

import chooser
import confidence
import extractor
import providers


def cand(code: str, name: str = "Опора знака дорожного", chars=None, score=0.9) -> dict:
    return {"code": code, "name": name, "characteristics": chars or [], "score": score}


def test_differing_keeps_only_characteristics_that_tell_codes_apart():
    a = cand("1", chars=[["Вид", ["Металлическая"]], ["Цвет", ["Серый"]]])
    b = cand("2", chars=[["Вид", ["Композитная"]], ["Цвет", ["Серый"]]])
    assert [c["characteristics"] for c in chooser.differing([a, b])] == [
        {"Вид": "Металлическая"},
        {"Вид": "Композитная"},
    ]


def test_answer_must_be_one_of_the_candidates():
    rows = [
        {"id": 7, "text": "Опора", "candidates": [cand("1"), cand("2")]},
        {"id": 8, "text": "Опора", "candidates": [cand("3"), cand("4")]},
    ]
    answer = json.dumps([{"id": 0, "code": "2"}, {"id": 1, "code": "99.99.99.999-00000001"}])
    assert chooser.parse(answer, rows, "x") == {7: ("2", "x")}  # an invented code is ignored
    with pytest.raises(ValueError):
        chooser.parse("не знаю", rows, "x")


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'initial.db'}")
    module = importlib.import_module("api")
    engine = sqlalchemy.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    models.Base.metadata.create_all(engine)
    monkeypatch.setattr(
        database, "SessionLocal", sqlalchemy.orm.sessionmaker(engine, expire_on_commit=False)
    )
    provider = providers.Provider("x", "openai", "https://x/v1", "key", "model")
    monkeypatch.setattr(extractor, "PROVIDERS", [provider])
    yield module
    engine.dispose()


def add_items(api, job_id, statuses):
    with database.SessionLocal() as session:
        session.add(models.Job(id=job_id, filename="spec.txt", status="processing"))
        for number, status in enumerate(statuses, start=1):
            session.add(
                models.Item(
                    job_id=job_id,
                    row_number=number,
                    raw_text=f"Опора знака композитная {number}",
                    candidates=[cand("1", score=0.95), cand("2", score=0.93)],
                    ktru_code="1",
                    ktru_name="Опора знака дорожного",
                    status=status,
                    summary="Поиск",
                )
            )
        session.commit()


def test_choice_changes_only_doubtful_rows_and_never_user_edits(api, monkeypatch):
    job_id = uuid.uuid4()
    add_items(api, job_id, ["needs_review", "auto", "corrected"])

    def pick_second(p, system, user, deadline, cancelled=None):
        assert system == chooser.SYSTEM_PROMPT
        return json.dumps([{"id": r["id"], "code": "2"} for r in json.loads(user)])

    monkeypatch.setattr(providers, "chat", pick_second)
    stages = {}
    spec_processing.choose_codes(job_id, stages, until=time.monotonic() + 30)
    with database.SessionLocal() as session:
        items = session.query(models.Item).order_by(models.Item.row_number).all()
    assert [i.ktru_code for i in items] == ["2", "1", "1"]
    assert items[0].status == "needs_review" and "нейросетью (x)" in items[0].summary
    assert items[0].confidence == confidence.for_code(items[0].candidates, "2")
    assert items[0].confidence < confidence.for_code(items[0].candidates, "1")
    assert items[0].zone == confidence.zone_of(items[0].confidence)
    assert items[0].ktru_name == "Опора знака дорожного"
    assert "Нейросеть выбрала этот код" in items[0].attributes["review"]
    assert "review" not in (items[1].attributes or {})  # the auto row is not touched
    assert "choose" in stages
    client = TestClient(api.app)
    detail = client.get(f"/api/items/{items[0].id}").json()
    chosen = next(c for c in detail["candidates"] if c["code"] == "2")
    assert detail["confidence"] == chosen["confidence"] == items[0].confidence
    listed = client.get(f"/api/jobs/{job_id}/items").json()["items"]
    assert listed[0]["confidence"] == detail["confidence"]


def test_no_provider_means_no_second_opinion(api, monkeypatch):
    job_id = uuid.uuid4()
    add_items(api, job_id, ["needs_review"])
    monkeypatch.setattr(extractor, "PROVIDERS", [])
    monkeypatch.setattr(providers, "chat", lambda *a, **k: pytest.fail("no request expected"))
    spec_processing.choose_codes(job_id, {}, until=time.monotonic() + 30)
    with database.SessionLocal() as session:
        assert session.query(models.Item).one().ktru_code == "1"


def test_choice_never_makes_the_job_late(api, monkeypatch):
    job_id = uuid.uuid4()
    add_items(api, job_id, ["needs_review"])
    monkeypatch.setattr(providers, "chat", lambda *a, **k: pytest.fail("no time left: no request"))
    spec_processing.choose_codes(job_id, {}, until=time.monotonic() - 1)
    with database.SessionLocal() as session:
        assert session.query(models.Item).one().ktru_code == "1"


def test_concurrent_manual_edit_keeps_its_code_and_unknown_score(api, monkeypatch):
    job_id = uuid.uuid4()
    add_items(api, job_id, ["needs_review"])

    def choose_while_edited(rows, cancelled, until):
        with database.SessionLocal() as session:
            item = session.query(models.Item).one()
            item.status, item.ktru_code, item.confidence = "corrected", "manual", None
            session.commit()
        return {rows[0]["id"]: ("2", "x")}

    monkeypatch.setattr(chooser, "choose", choose_while_edited)
    spec_processing.choose_codes(job_id, {}, until=time.monotonic() + 30)
    with database.SessionLocal() as session:
        item = session.query(models.Item).one()
        assert item.status == "corrected" and item.ktru_code == "manual"
        assert item.confidence is None


def test_invalid_second_opinion_does_not_change_the_proposal(api, monkeypatch):
    job_id = uuid.uuid4()
    add_items(api, job_id, ["needs_review"])
    monkeypatch.setattr(chooser, "choose", lambda rows, *args: {rows[0]["id"]: ("unknown", "x")})
    spec_processing.choose_codes(job_id, {}, until=time.monotonic() + 30)
    with database.SessionLocal() as session:
        assert session.query(models.Item).one().ktru_code == "1"


@pytest.mark.parametrize("action", ["undo-code", "revert"])
def test_old_automatic_snapshot_restores_selected_code_percentage(api, action):
    job_id = uuid.uuid4()
    add_items(api, job_id, ["corrected"])
    with database.SessionLocal() as session:
        item = session.query(models.Item).one()
        snapshot = {"ktru_code": "2", "confidence": None, "zone": "low", "status": "needs_review"}
        item.attributes = (
            {"history": [snapshot]} if action == "undo-code" else {"original": snapshot}
        )
        session.commit()
        item_id, candidates = item.id, item.candidates
    response = TestClient(api.app).post(f"/api/items/{item_id}/{action}")
    assert response.status_code == 200
    assert response.json()["confidence"] == confidence.for_code(candidates, "2")
    assert response.json()["ktru_code"] == "2"
    assert response.json()["status"] == "needs_review"


import sqlalchemy
import sqlalchemy.orm

import database
import models
import spec_processing
