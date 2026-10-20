"""Second opinion from the top-5: only candidate codes, only doubtful rows, user edits are kept."""

import importlib
import json
import time
import uuid

import pytest

import chooser
import extractor
import providers


def cand(code: str, name: str = "Опора знака дорожного", chars=None) -> dict:
    return {"code": code, "name": name, "characteristics": chars or []}


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
    engine = module.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    module.Base.metadata.create_all(engine)
    monkeypatch.setattr(module, "SessionLocal", module.sessionmaker(engine, expire_on_commit=False))
    provider = providers.Provider("x", "openai", "https://x/v1", "key", "model")
    monkeypatch.setattr(extractor, "PROVIDERS", [provider])
    yield module
    engine.dispose()


def add_items(api, job_id, statuses):
    with api.SessionLocal() as session:
        session.add(api.Job(id=job_id, filename="spec.txt", status="processing"))
        for number, status in enumerate(statuses, start=1):
            session.add(
                api.Item(
                    job_id=job_id,
                    row_number=number,
                    raw_text=f"Опора знака композитная {number}",
                    candidates=[cand("1"), cand("2")],
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
    api.choose_codes(job_id, stages, until=time.monotonic() + 30)
    with api.SessionLocal() as session:
        items = session.query(api.Item).order_by(api.Item.row_number).all()
    assert [i.ktru_code for i in items] == ["2", "1", "1"]
    assert items[0].status == "needs_review" and "нейросетью (x)" in items[0].summary
    assert "choose" in stages


def test_no_provider_means_no_second_opinion(api, monkeypatch):
    job_id = uuid.uuid4()
    add_items(api, job_id, ["needs_review"])
    monkeypatch.setattr(extractor, "PROVIDERS", [])
    monkeypatch.setattr(providers, "chat", lambda *a, **k: pytest.fail("no request expected"))
    api.choose_codes(job_id, {}, until=time.monotonic() + 30)
    with api.SessionLocal() as session:
        assert session.query(api.Item).one().ktru_code == "1"


def test_choice_never_makes_the_job_late(api, monkeypatch):
    job_id = uuid.uuid4()
    add_items(api, job_id, ["needs_review"])
    monkeypatch.setattr(providers, "chat", lambda *a, **k: pytest.fail("no time left: no request"))
    api.choose_codes(job_id, {}, until=time.monotonic() - 1)
    with api.SessionLocal() as session:
        assert session.query(api.Item).one().ktru_code == "1"
