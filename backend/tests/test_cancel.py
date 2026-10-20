"""Stopping a job: the queue and waiting LLM requests are dropped, finished rows stay."""

import collections
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
    monkeypatch.setattr(extractor, "CREDENTIALS", "")  # rules only, no network
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
        bounds=[[[], []], [[], []]],
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


def test_request_waiting_for_a_slot_is_not_sent_after_cancel(monkeypatch):
    sent = []
    monkeypatch.setattr(extractor._client, "post", lambda *a, **k: sent.append(a))
    stop = threading.Event()
    stop.set()
    with pytest.raises(extractor.Cancelled):
        extractor.post_chat({}, time.monotonic() + 5, stop)
    assert not sent


def test_rows_of_a_cancelled_chunk_are_not_extracted():
    stop = threading.Event()
    stop.set()
    with pytest.raises(extractor.Cancelled):
        extractor.extract_chunk(["Бумага"], time.monotonic() + 5, stop)


def test_request_slots_are_served_in_arrival_order():
    slots = extractor.FairSlots(1)
    assert slots.acquire(1)
    order, threads = [], []
    for n in range(5):
        thread = threading.Thread(target=lambda n=n: slots.acquire(5) and order.append(n))
        thread.start()
        threads.append(thread)
        time.sleep(0.05)  # arrival order 0..4
    for _ in range(5):
        slots.release()  # the releasing side never jumps the queue
        time.sleep(0.05)
    for thread in threads:
        thread.join(1)
    assert order == [0, 1, 2, 3, 4]
    assert not slots.acquire(0.05) and slots._queue == collections.deque()
