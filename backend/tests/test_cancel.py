"""Stopping a job: the queue and waiting LLM requests are dropped, finished rows stay.
Also deleting a job and undoing accept or a manual code."""

import importlib
import threading
import time
import uuid
from contextlib import asynccontextmanager

import numpy as np
import pytest
from fastapi.testclient import TestClient

import embedder
import extractor
import search


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'initial.db'}")
    module = importlib.import_module("api")
    engine = module.create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    module.Base.metadata.create_all(engine)
    monkeypatch.setattr(module, "SessionLocal", module.sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(module, "UPLOAD_DIR", tmp_path)

    @asynccontextmanager
    async def lifespan(_):  # no workers: the test drives the job itself
        yield

    monkeypatch.setattr(module.app.router, "lifespan_context", lifespan)
    monkeypatch.setattr(extractor, "PROVIDERS", [])  # rules only, no network
    yield module
    engine.dispose()


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
    with api.SessionLocal() as session:
        session.add(api.Job(id=done, filename="a.txt", status="done"))
        session.add(api.Job(id=stopped, filename="b.txt", status="cancelled"))
        session.commit()
    with TestClient(api.app) as client:
        assert client.post(f"/api/jobs/{done}/cancel").json()["status"] == "done"
    api.finish(stopped, "done", None, {"total": 1.0})
    with api.SessionLocal() as session:
        job = session.get(api.Job, stopped)
        assert job.status == "cancelled" and job.stage_seconds == {"total": 1.0}


def test_delete_job_removes_rows_and_file_but_not_a_running_job(api, tmp_path):
    done, running = uuid.uuid4(), uuid.uuid4()
    with api.SessionLocal() as session:
        session.add(api.Job(id=done, filename="a.txt", status="done"))
        session.add(api.Job(id=running, filename="b.txt", status="processing"))
        session.add(api.Item(job_id=done, row_number=1, raw_text="x", status="auto"))
        session.commit()
    (tmp_path / f"{done}.txt").write_text("x")
    with TestClient(api.app) as client:
        assert client.delete(f"/api/jobs/{running}").status_code == 409
        assert client.delete(f"/api/jobs/{done}").status_code == 204
        assert client.get(f"/api/jobs/{done}").status_code == 404
        assert client.delete(f"/api/jobs/{done}").status_code == 404
        assert client.get(f"/api/jobs/{running}").status_code == 200
    with api.SessionLocal() as session:
        assert session.query(api.Item).count() == 0
    assert not (tmp_path / f"{done}.txt").exists()


def test_revert_returns_the_proposed_code_and_status(api, monkeypatch):
    monkeypatch.setattr(api.state, "catalog", tiny_catalog())
    job_id = uuid.uuid4()
    proposed = {"ktru_code": "17.12.14.129-00000001", "ktru_name": "Бумага для офисной техники"}
    with api.SessionLocal() as session:
        session.add(api.Job(id=job_id, filename="a.txt", status="done"))
        session.add_all(
            api.Item(
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
        first, second = (item.id for item in session.query(api.Item).order_by(api.Item.id))
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
    with api.SessionLocal() as session:
        session.add_all([api.Job(id=job_id, filename="a.txt"), api.Job(id=other, filename="b.txt")])
        session.add_all(
            api.Item(job_id=job_id, row_number=n, raw_text="x", status=status)
            for n, status in enumerate(statuses, 1)
        )
        session.add(api.Item(job_id=other, row_number=1, raw_text="y", status="auto"))
        session.commit()
        ids = [i.id for i in session.query(api.Item).filter_by(job_id=job_id).order_by(api.Item.id)]
    with TestClient(api.app) as client:
        first = client.get(f"/api/items/{ids[0]}").json()
        assert (first["position"], first["total"], first["prev_id"]) == (1, 5, None)
        assert (first["next_id"], first["next_unreviewed_id"]) == (ids[1], ids[2])
        middle = client.get(f"/api/items/{ids[2]}").json()
        assert (middle["prev_id"], middle["next_id"]) == (ids[1], ids[3])
        assert middle["next_unreviewed_id"] == ids[4]  # the corrected row is skipped
        last = client.get(f"/api/items/{ids[4]}").json()
        assert (last["next_id"], last["next_unreviewed_id"]) == (None, None)
        assert client.get("/api/items/99999").status_code == 404


def test_job_list_has_the_counts_of_each_job(api):
    first, second = uuid.uuid4(), uuid.uuid4()
    with api.SessionLocal() as session:
        session.add_all([api.Job(id=first, filename="a.txt"), api.Job(id=second, filename="b.txt")])
        session.add_all(
            api.Item(job_id=first, row_number=n, raw_text="x", status=status)
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


def test_run_job_stops_between_batches_and_keeps_finished_rows(api, monkeypatch, tmp_path):
    monkeypatch.setattr(api.state, "catalog", tiny_catalog())
    monkeypatch.setattr(embedder, "embed", lambda texts: None)
    job_id = uuid.uuid4()
    with api.SessionLocal() as session:
        session.add(api.Job(id=job_id, filename="spec.txt", status="processing"))
        session.commit()
    path = tmp_path / f"{job_id}.txt"
    path.write_text("\n".join(f"Бумага А4 {i}" for i in range(api.BATCH * 3)), encoding="utf-8")
    original = api.process_batch

    def first_batch_then_stop(job, batch, start, *args):
        original(job, batch, start, *args)
        with api.SessionLocal() as session:  # the user presses "stop" after the first batch
            session.execute(api.update(api.Job).where(api.Job.id == job).values(status="cancelled"))
            session.commit()

    monkeypatch.setattr(api, "process_batch", first_batch_then_stop)
    api.run_job(job_id)
    with api.SessionLocal() as session:
        job = session.get(api.Job, job_id)
        count = session.query(api.Item).filter(api.Item.job_id == job_id).count()
    assert job.status == "cancelled" and job.processed == count == api.BATCH
    assert not path.exists()
    assert job_id not in api.state.cancels


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
