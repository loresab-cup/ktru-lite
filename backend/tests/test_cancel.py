"""Stopping a job: the queue and waiting LLM requests are dropped, finished rows stay.
Also deleting a job and undoing accept or a manual code."""

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

import embedder
import extractor
import search


@pytest.fixture
def api(api_database):
    return api_database


def tiny_catalog() -> search.Catalog:
    names = ["Бумага для офисной техники", "Ноутбук"]
    return search.Catalog(
        vectors=np.asarray([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32),
        codes=["17.12.14.129-00000001", "26.20.11.110-00000001"],
        statuses=["ACTIVE", "ACTIVE"],
        names=names,
        okpd2=["", ""],
        code_words=[" ".join(search.words(name)) for name in names],
        chars=[[], []],
        bounds=[[], []],
        fixed=[[], []],
        templates=[False, False],
        units=[[], []],
    )


def test_cancel_queued_job_drops_file_and_allows_export(api, tmp_path):
    with TestClient(api.app) as client:
        job_id = client.post("/api/jobs", files={"file": ("a.txt", "Бумага".encode())}).json()[
            "job_id"
        ]
        assert list(tmp_path.glob(f"{job_id}*"))
        body = client.post(f"/api/jobs/{job_id}/cancel").json()
        assert body["status"] == "cancelled" and body["finished_at"]
        assert not list(tmp_path.glob(f"{job_id}*"))
        assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "cancelled"
        assert client.get(f"/api/jobs/{job_id}/export", params={"format": "csv"}).status_code == 200
        assert client.post(f"/api/jobs/{uuid.uuid4()}/cancel").status_code == 404


def test_finished_job_cannot_be_cancelled_and_done_never_overwrites_cancel(api):
    done, stopped = uuid.uuid4(), uuid.uuid4()
    with database.SessionLocal() as session:
        session.add(models.Job(id=done, filename="a.txt", status="done"))
        session.add(models.Job(id=stopped, filename="b.txt", status="cancelled"))
        session.commit()
    with TestClient(api.app) as client:
        assert client.post(f"/api/jobs/{done}/cancel").json()["status"] == "done"
    job_store.finish(stopped, "done", None, {"total": 1.0})
    with database.SessionLocal() as session:
        job = session.get(models.Job, stopped)
        assert job.status == "cancelled" and job.stage_seconds == {"total": 1.0}


def test_delete_job_removes_rows_and_file_but_not_a_running_job(api, tmp_path):
    done, running = uuid.uuid4(), uuid.uuid4()
    with database.SessionLocal() as session:
        session.add(models.Job(id=done, filename="a.txt", status="done"))
        session.add(models.Job(id=running, filename="b.txt", status="processing"))
        session.add(models.Item(job_id=done, row_number=1, raw_text="x", status="auto"))
        session.commit()
    (tmp_path / f"{done}.txt").write_text("x")
    with TestClient(api.app) as client:
        assert client.delete(f"/api/jobs/{running}").status_code == 409
        assert client.delete(f"/api/jobs/{done}").status_code == 204
        assert client.get(f"/api/jobs/{done}").status_code == 404
        assert client.delete(f"/api/jobs/{done}").status_code == 404
        assert client.get(f"/api/jobs/{running}").status_code == 200
    with database.SessionLocal() as session:
        assert session.query(models.Item).count() == 0
    assert not (tmp_path / f"{done}.txt").exists()


def test_a_job_stopped_a_moment_ago_says_it_is_stopping(api):
    stopping = uuid.uuid4()
    with database.SessionLocal() as session:
        session.add(models.Job(id=stopping, filename="c.txt", status="cancelled"))
        session.commit()
    runtime.state.cancels[stopping] = threading.Event()  # the worker has not finished its batch
    try:
        with TestClient(api.app) as client:
            answer = client.delete(f"/api/jobs/{stopping}")
            assert answer.status_code == 409 and "останавливается" in answer.json()["detail"]
    finally:
        runtime.state.cancels.pop(stopping, None)
    with TestClient(api.app) as client:
        assert client.delete(f"/api/jobs/{stopping}").status_code == 204


def test_revert_returns_the_proposed_code_and_status(api, monkeypatch):
    monkeypatch.setattr(runtime.state, "catalog", tiny_catalog())
    job_id = uuid.uuid4()
    proposed = {"ktru_code": "17.12.14.129-00000001", "ktru_name": "Бумага для офисной техники"}
    with database.SessionLocal() as session:
        session.add(models.Job(id=job_id, filename="a.txt", status="done"))
        session.add_all(
            models.Item(
                job_id=job_id,
                row_number=n,
                raw_text="x",
                confidence=70.0,
                zone="medium",
                status="needs_review",
                summary="Выбран сетью",
                attributes={"name": "бумага"},
                candidates=[{"code": proposed["ktru_code"], "score": 0.8, "status": "ACTIVE"}],
                **proposed,
            )
            for n in (1, 2)
        )
        session.commit()
        first, second = (item.id for item in session.query(models.Item).order_by(models.Item.id))
    with TestClient(api.app) as client:
        assert client.post(f"/api/items/{first}/revert").json()["status"] == "needs_review"
        client.post(f"/api/items/{first}/accept")
        fixed = client.patch(f"/api/items/{first}", json={"ktru_code": "26.20.11.110-00000001"})
        assert fixed.json()["status"] == "corrected"
        back = client.post(f"/api/items/{first}/revert").json()
        assert back["status"] == "needs_review" and back["ktru_code"] == proposed["ktru_code"]
        assert back["confidence"] == 70.0 and back["summary"] == "Выбран сетью"
        assert "original" not in back["attributes"]
        assert client.post(f"/api/items/{first}/revert").json() == back  # twice is harmless
        # accepted in bulk: the snapshot is taken as well
        client.post(f"/api/jobs/{job_id}/accept", params={"min_confidence": 0})
        assert client.post(f"/api/items/{second}/revert").json()["summary"] == "Выбран сетью"
        assert client.post("/api/items/99999/revert").status_code == 404


def test_item_page_knows_its_neighbours(api):
    job_id, other = uuid.uuid4(), uuid.uuid4()
    statuses = ["auto", "accepted", "needs_review", "corrected", "needs_review"]
    with database.SessionLocal() as session:
        session.add_all(
            [models.Job(id=job_id, filename="a.txt"), models.Job(id=other, filename="b.txt")]
        )
        session.add_all(
            models.Item(job_id=job_id, row_number=n, raw_text="x", status=status)
            for n, status in enumerate(statuses, 1)
        )
        session.add(models.Item(job_id=other, row_number=1, raw_text="y", status="auto"))
        session.commit()
        ids = [
            i.id
            for i in session.query(models.Item).filter_by(job_id=job_id).order_by(models.Item.id)
        ]
    with TestClient(api.app) as client:
        first = client.get(f"/api/items/{ids[0]}").json()
        assert (first["position"], first["total"], first["prev_id"]) == (1, 5, None)
        assert (first["next_id"], first["next_unreviewed_id"]) == (ids[1], ids[2])
        middle = client.get(f"/api/items/{ids[2]}").json()
        assert (middle["prev_id"], middle["next_id"]) == (ids[1], ids[3])
        assert middle["next_unreviewed_id"] == ids[4]  # the corrected row is skipped
        last = client.get(f"/api/items/{ids[4]}").json()
        assert (last["next_id"], last["next_unreviewed_id"]) == (None, ids[2])
        with database.SessionLocal() as session:
            for item in session.query(models.Item).filter_by(job_id=job_id):
                item.status = "accepted"
            session.commit()
        assert client.get(f"/api/items/{ids[4]}").json()["next_unreviewed_id"] is None
        assert client.get("/api/items/99999").status_code == 404


def test_item_list_can_leave_the_candidates_out(api):
    job_id = uuid.uuid4()
    with database.SessionLocal() as session:
        session.add(models.Job(id=job_id, filename="a.txt"))
        session.add(
            models.Item(
                job_id=job_id, row_number=1, raw_text="x", status="auto", candidates=[{"c": 1}]
            )
        )
        session.commit()
    with TestClient(api.app) as client:
        full = client.get(f"/api/jobs/{job_id}/items").json()["items"][0]
        light = client.get(f"/api/jobs/{job_id}/items", params={"candidates": "false"}).json()
    assert full["candidates"] == [{"c": 1}]
    assert "candidates" not in light["items"][0] and light["total"] == 1


def test_job_list_has_the_counts_of_each_job(api):
    first, second = uuid.uuid4(), uuid.uuid4()
    with database.SessionLocal() as session:
        session.add_all(
            [models.Job(id=first, filename="a.txt"), models.Job(id=second, filename="b.txt")]
        )
        session.add_all(
            models.Item(job_id=first, row_number=n, raw_text="x", status=status)
            for n, status in enumerate(["auto", "auto", "accepted"], 1)
        )
        session.commit()
    with TestClient(api.app) as client:
        jobs = {job["id"]: job for job in client.get("/api/jobs").json()}
        one = client.get("/api/jobs", params={"limit": 1})
        assert len(one.json()) == 1 and one.headers["X-Total-Count"] == "2"  # the real number
        assert client.get("/api/jobs", params={"limit": 1001}).status_code == 422
    assert jobs[str(first)]["counts"] == {
        "auto": 2,
        "needs_review": 0,
        "corrected": 0,
        "accepted": 1,
        "failed": 0,
    }
    assert sum(jobs[str(second)]["counts"].values()) == 0


def test_job_list_finds_by_name_and_sorts(api):
    start = datetime(2026, 10, 1, tzinfo=UTC)
    names = ["spec_1000.xlsx", "Spec_2000.xlsx", "beta.csv", "Alpha.txt"]  # uploaded in this order
    with database.SessionLocal() as session:
        session.add_all(
            models.Job(id=uuid.uuid4(), filename=name, created_at=start + timedelta(minutes=i))
            for i, name in enumerate(names)
        )
        session.commit()
    with TestClient(api.app) as client:

        def listed(**params) -> list[str]:
            return [job["filename"] for job in client.get("/api/jobs", params=params).json()]

        assert listed() == names[::-1]  # newest first
        assert listed(sort="old") == names
        assert listed(sort="name") == ["Alpha.txt", "beta.csv", "spec_1000.xlsx", "Spec_2000.xlsx"]
        assert listed(sort="name_desc") == [
            "Spec_2000.xlsx",
            "spec_1000.xlsx",
            "beta.csv",
            "Alpha.txt",
        ]
        assert listed(q="SPEC") == ["Spec_2000.xlsx", "spec_1000.xlsx"]  # case does not matter
        found = client.get("/api/jobs", params={"q": "c_1"})  # «_» is a letter, not a wildcard
        assert [j["filename"] for j in found.json()] == ["spec_1000.xlsx"]
        assert found.headers["X-Total-Count"] == "1"
        assert client.get("/api/jobs", params={"sort": "size"}).status_code == 422


def test_run_job_stops_between_batches_and_keeps_finished_rows(api, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.state, "catalog", tiny_catalog())
    monkeypatch.setattr(embedder, "embed", lambda texts: None)
    job_id = uuid.uuid4()
    with database.SessionLocal() as session:
        session.add(models.Job(id=job_id, filename="spec.txt", status="processing"))
        session.commit()
    path = tmp_path / f"{job_id}.txt"
    path.write_text("\n".join(f"Бумага А4 {i}" for i in range(runtime.BATCH * 3)), encoding="utf-8")
    original = spec_processing.process_batch

    def first_batch_then_stop(job, batch, start, *args):
        original(job, batch, start, *args)
        with database.SessionLocal() as session:  # the user presses "stop" after the first batch
            session.execute(
                sqlalchemy.update(models.Job).where(models.Job.id == job).values(status="cancelled")
            )
            session.commit()

    monkeypatch.setattr(spec_processing, "process_batch", first_batch_then_stop)
    spec_processing.run_job(job_id)
    with database.SessionLocal() as session:
        job = session.get(models.Job, job_id)
        count = session.query(models.Item).filter(models.Item.job_id == job_id).count()
    assert job.status == "cancelled" and job.processed == count == runtime.BATCH
    assert not path.exists()
    assert job_id not in runtime.state.cancels


def test_vectors_of_the_next_batch_are_fetched_during_the_search(api, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.state, "catalog", tiny_catalog())
    second_embedded = threading.Event()
    calls = []

    def embed(texts):
        calls.append(texts)
        if len(calls) == 2:
            second_embedded.set()

    monkeypatch.setattr(embedder, "embed", embed)
    job_id = uuid.uuid4()
    with database.SessionLocal() as session:
        session.add(models.Job(id=job_id, filename="spec.txt", status="processing"))
        session.commit()
    path = tmp_path / f"{job_id}.txt"
    path.write_text("\n".join(f"Бумага А4 {i}" for i in range(runtime.BATCH * 2)), encoding="utf-8")
    original = spec_processing.process_batch
    overlapped = []

    def slow_first_batch(job, batch, start, *args):
        if start == 0:  # the search of the first batch: the second one is embedded meanwhile
            overlapped.append(second_embedded.wait(5))
        original(job, batch, start, *args)

    monkeypatch.setattr(spec_processing, "process_batch", slow_first_batch)
    spec_processing.run_job(job_id)
    with database.SessionLocal() as session:
        job = session.get(models.Job, job_id)
    assert overlapped == [True]
    assert job.status == "done" and job.processed == runtime.BATCH * 2
    assert {"extract", "embed", "wait", "search", "save"} <= set(job.stage_seconds)


def test_cancel_unblocks_a_row_waiting_for_the_network(monkeypatch):
    release = threading.Event()

    def slow_chunk(texts, deadline, cancelled):
        release.wait(5)
        return [extractor.rules(text) for text in texts]

    monkeypatch.setattr(extractor, "extract_chunk", slow_chunk)
    llm = extractor.JobExtractor(["Бумага А4"])
    threading.Timer(0.2, llm.cancel).start()
    started = time.monotonic()
    with pytest.raises(extractor.Cancelled):
        llm.get("Бумага А4")
    assert time.monotonic() - started < 2
    release.set()


def test_rows_of_a_cancelled_chunk_are_not_extracted():
    stop = threading.Event()
    stop.set()
    with pytest.raises(extractor.Cancelled):
        extractor.extract_chunk(["Бумага"], time.monotonic() + 5, stop)


def test_one_decision_goes_to_the_identical_rows_that_wait(api, monkeypatch):
    monkeypatch.setattr(runtime.state, "catalog", tiny_catalog())
    job_id = uuid.uuid4()
    proposed = {"ktru_code": "17.12.14.129-00000001", "ktru_name": "Бумага для офисной техники"}
    rows = [("Ноутбук 16 ГБ", "needs_review"), ("Ноутбук 16 ГБ", "needs_review")]
    rows += [("Ноутбук 16 ГБ", "auto"), ("Бумага А4", "needs_review")]
    with database.SessionLocal() as session:
        session.add(models.Job(id=job_id, filename="a.txt", status="done"))
        for n, (text, status) in enumerate(rows, start=1):
            session.add(
                models.Item(
                    job_id=job_id,
                    row_number=n,
                    raw_text=text,
                    status=status,
                    confidence=60.0,
                    zone="medium",
                    summary="Поиск",
                    **proposed,
                )
            )
        session.commit()
        ids = [item.id for item in session.query(models.Item).order_by(models.Item.id)]
    with TestClient(api.app) as client:
        assert client.get(f"/api/items/{ids[0]}").json()["same_pending"] == 1
        assert client.post(f"/api/items/{ids[0]}/apply-same").status_code == 400  # not decided
        client.patch(f"/api/items/{ids[0]}", json={"ktru_code": "26.20.11.110-00000001"})
        assert client.post(f"/api/items/{ids[0]}/apply-same").json() == {"applied": 1}
        twin = client.get(f"/api/items/{ids[1]}").json()
        assert twin["status"] == "corrected" and twin["ktru_code"] == "26.20.11.110-00000001"
        assert twin["confidence"] is None and "строки 1" in twin["summary"]
        assert client.get(f"/api/items/{ids[2]}").json()["status"] == "auto"  # not waiting
        assert client.get(f"/api/items/{ids[3]}").json()["status"] == "needs_review"
        back = client.post(f"/api/items/{ids[1]}/undo-code").json()
        assert back["ktru_code"] == proposed["ktru_code"]
        assert back["status"] == "needs_review"  # undone: it waits again
        assert client.get(f"/api/items/{ids[0]}").json()["same_pending"] == 1


def test_a_persons_code_goes_to_the_same_row_of_a_later_file(api, monkeypatch):
    monkeypatch.setattr(runtime.state, "catalog", tiny_catalog())
    job_id = uuid.uuid4()
    proposed = {"ktru_code": "17.12.14.129-00000001", "ktru_name": "Бумага для офисной техники"}
    found = [
        {
            "code": proposed["ktru_code"],
            "name": proposed["ktru_name"],
            "score": 0.8,
            "raw_score": 0.8,
            "status": "ACTIVE",
        }
    ]
    with database.SessionLocal() as session:
        session.add(models.Job(id=job_id, filename="a.txt", status="done"))
        session.add(
            models.Item(
                job_id=job_id,
                row_number=1,
                raw_text="Ноутбук  16 ГБ",
                status="needs_review",
                confidence=60.0,
                zone="medium",
                summary="Поиск",
                candidates=found,
                **proposed,
            )
        )
        session.commit()
        first = session.query(models.Item).one().id
    later = parser.Item(5, "ноутбук 16 гб", {})
    with TestClient(api.app) as client:
        client.patch(f"/api/items/{first}", json={"ktru_code": "26.20.11.110-00000001"})
        [decision] = decisions.recalled([later]).values()
        item = spec_processing.build_item(uuid.uuid4(), later, {"name": "ноутбук"}, found, decision)
        assert item.ktru_code == "26.20.11.110-00000001" and item.status == "auto"
        assert item.confidence is None and "решения человека" in item.summary
        assert "review" not in item.attributes
        client.post(f"/api/items/{first}/revert")  # back to review: the decision is taken back
        assert decisions.recalled([later]) == {}
        client.post(f"/api/items/{first}/accept")  # a person agreed with a doubtful row
        assert (
            decisions.recalled([later])[decisions.text_key(later.text)].ktru_code
            == proposed["ktru_code"]
        )
        response = client.post(f"/api/jobs/{job_id}/revert", json={"all_accepted": True})
        assert response.json() == {"reverted": 1}
        assert decisions.recalled([later]) == {}
        client.patch(f"/api/items/{first}", json={"ktru_code": "26.20.11.110-00000001"})
        response = client.post(f"/api/jobs/{job_id}/revert", json={"ids": [first]})
        assert response.json() == {"reverted": 1}
        assert decisions.recalled([later]) == {}
        client.post(f"/api/items/{first}/accept")
        assert client.delete(f"/api/jobs/{job_id}").status_code == 204
        assert decisions.recalled([later]) == {}  # the job is gone, so are its decisions
    monkeypatch.setattr(runtime, "REMEMBER", False)
    assert decisions.recalled([later]) == {}


def test_old_finished_jobs_are_cleaned_only_when_asked(api, monkeypatch, tmp_path):
    old, fresh, running = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    long_ago = datetime.now(UTC) - timedelta(days=40)
    with database.SessionLocal() as session:
        session.add(models.Job(id=old, filename="a.txt", status="done", created_at=long_ago))
        session.add(models.Job(id=fresh, filename="b.txt", status="done"))
        session.add(
            models.Job(id=running, filename="c.txt", status="processing", created_at=long_ago)
        )
        session.add(models.Item(job_id=old, row_number=1, raw_text="x", status="auto"))
        session.add(models.Decision(key="k", job_id=old, ktru_code="17.12.14.129-00000001"))
        session.commit()
    (tmp_path / f"{old}.txt").write_text("x")
    assert app_lifecycle.clean_old_jobs() == 0  # JOBS_KEEP_DAYS=0 by default: nothing is deleted
    monkeypatch.setattr(runtime, "KEEP_DAYS", 30.0)
    assert app_lifecycle.clean_old_jobs() == 1
    with database.SessionLocal() as session:
        assert {job.id for job in session.query(models.Job)} == {fresh, running}
        assert session.query(models.Item).count() == 0
        assert session.query(models.Decision).count() == 1  # the memory of decisions stays
    assert not (tmp_path / f"{old}.txt").exists()


def test_a_failed_batch_stops_the_network_requests_of_the_job(api, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.state, "catalog", tiny_catalog())
    monkeypatch.setattr(embedder, "embed", lambda texts: None)
    stopped = []
    monkeypatch.setattr(extractor.JobExtractor, "cancel", lambda self: stopped.append(True))
    job_id = uuid.uuid4()
    with database.SessionLocal() as session:
        session.add(models.Job(id=job_id, filename="spec.txt", status="processing"))
        session.commit()
    path = tmp_path / f"{job_id}.txt"
    path.write_text("Бумага А4\nНоутбук", encoding="utf-8")

    def broken(*args):
        raise RuntimeError("the database went away")

    monkeypatch.setattr(spec_processing, "process_batch", broken)
    spec_processing.run_job(job_id)
    with database.SessionLocal() as session:
        assert session.get(models.Job, job_id).status == "failed"
    assert stopped == [True]


import sqlalchemy
import sqlalchemy.orm

import app_lifecycle
import database
import decisions
import job_store
import models
import parser
import runtime
import spec_processing
