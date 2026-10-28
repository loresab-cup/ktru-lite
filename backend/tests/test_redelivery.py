"""Retrying a specification must preserve the user's settled positions."""

import uuid
from types import SimpleNamespace

import database
import pipeline
import runtime
from models import Item, Job


def test_retry_keeps_manual_row_and_does_not_duplicate_it(api_database, monkeypatch):
    job_id = uuid.uuid4()
    catalog = SimpleNamespace(
        search=lambda vectors, texts, *args, **kwargs: [
            [{"code": "paper", "name": "Бумага", "status": "ACTIVE", "score": 0.7}] for _ in texts
        ]
    )
    monkeypatch.setattr(runtime.state, "catalog", catalog)
    with database.SessionLocal() as s:
        s.add(Job(id=job_id, filename="retry.txt", status="processing"))
        s.flush()
        row = Item(
            job_id=job_id,
            row_number=1,
            raw_text="Бумага",
            status="corrected",
            ktru_code="human-choice",
            ktru_name="Выбрано человеком",
            confidence=None,
            attributes={"name": "Бумага"},
            summary="Ручная правка",
        )
        s.add(row)
        s.commit()
        row_id = row.id
    (runtime.UPLOAD_DIR / f"{job_id}.txt").write_text("Бумага\nНоутбук", encoding="utf-8")
    pipeline.run_job(job_id)
    with database.SessionLocal() as s:
        assert s.get(Job, job_id).status == "done"
        assert s.query(Item).filter(Item.job_id == job_id).count() == 2
        row = s.get(Item, row_id)
        assert (
            row.ktru_code == "human-choice" and row.status == "corrected" and row.confidence is None
        )
