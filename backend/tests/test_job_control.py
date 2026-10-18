"""Job lifecycle and cancellation, using barriers rather than timing assumptions."""

import importlib
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi.testclient import TestClient

import embedder
import extractor
import parser


class Catalog:
    def search(self, vectors, queries, *args, **kwargs):
        return [[] for _ in queries]

    def get(self, code):
        return {"code": code, "name": "Test code", "status": "ACTIVE"}


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'initial.db'}")
    api = importlib.import_module("api")
    engine = api.create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    api.Base.metadata.create_all(engine)
    monkeypatch.setattr(api, "SessionLocal", api.sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(api, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(api.state, "catalog", Catalog())
    monkeypatch.setattr(api.state, "cancellations", {})
    api.state.stop.clear()
    monkeypatch.setattr(embedder, "embed", lambda texts, **kwargs: None)
    monkeypatch.setattr(extractor, "llm_state", lambda: "off")

    @asynccontextmanager
    async def lifespan(_):
        yield

    monkeypatch.setattr(api.app.router, "lifespan_context", lifespan)
    yield api
    engine.dispose()


def upload(client, count=201):
    data = "\n".join(f"Бумага офисная А4 {i}" for i in range(count)).encode()
    response = client.post("/api/jobs", files={"file": ("spec.txt", data)})
    assert response.status_code == 202
    return response.json()["job_id"]


def launch(api, client, job_id, pool):
    assert client.post(f"/api/jobs/{job_id}/start").status_code == 200
    claimed = api.claim_job()
    assert str(claimed) == job_id
    return pool.submit(api.run_job, claimed)


def test_upload_requires_explicit_start_and_start_is_idempotent(app):
    with TestClient(app.app) as client:
        job_id = upload(client)
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "uploaded"
        assert app.claim_job() is None
        for _ in range(2):
            assert client.post(f"/api/jobs/{job_id}/start").status_code == 200
        assert str(app.claim_job()) == job_id
        assert app.claim_job() is None
        assert client.delete(f"/api/jobs/{job_id}").status_code == 409
        other = upload(client)
        assert client.delete(f"/api/jobs/{other}").status_code == 200
        assert client.get(f"/api/jobs/{other}").status_code == 404


def test_stop_queued_job_never_claims_it(app):
    with TestClient(app.app) as client:
        job_id = upload(client)
        client.post(f"/api/jobs/{job_id}/start")
        for _ in range(2):
            assert client.post(f"/api/jobs/{job_id}/stop").json()["status"] == "stopped"
        assert app.claim_job() is None
        assert client.post(f"/api/jobs/{job_id}/start").status_code == 409


def test_stop_during_file_read_does_not_extract(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = parser.parse_file

    def parse(path, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(path, **kwargs)

    monkeypatch.setattr(parser, "parse_file", parse)
    monkeypatch.setattr(
        extractor, "JobExtractor", lambda *a, **kw: pytest.fail("extraction started")
    )
    with TestClient(app.app) as client, ThreadPoolExecutor(1) as pool:
        job_id = upload(client)
        work = launch(app, client, job_id, pool)
        try:
            assert entered.wait(5)
            assert client.post(f"/api/jobs/{job_id}/stop").json()["status"] == "stopping"
        finally:
            release.set()
        work.result(5)
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "stopped"


@pytest.mark.parametrize("stage", ["embed", "search", "failure"])
def test_partial_results_and_corrections_survive_stop_or_failure(app, monkeypatch, stage):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def block(queries):
        calls.append(len(queries))
        if len(calls) == 2:
            entered.set()
            assert release.wait(5)
            if stage == "failure":
                raise RuntimeError("synthetic search failure")

    if stage == "embed":

        def embed(queries, **kwargs):
            block(queries)

        monkeypatch.setattr(embedder, "embed", embed)
    else:

        def search(vectors, queries, *args, **kwargs):
            block(queries)
            return [[] for _ in queries]

        monkeypatch.setattr(app.state.catalog, "search", search)

    with TestClient(app.app) as client, ThreadPoolExecutor(1) as pool:
        job_id = upload(client)
        work = launch(app, client, job_id, pool)
        try:
            assert entered.wait(5)
            job = client.get(f"/api/jobs/{job_id}").json()
            assert job["status"] == "processing" and job["processed"] == 100
            items = client.get(f"/api/jobs/{job_id}/items?limit=200").json()["items"]
            first = items[0]
            code = "17.12.14.129-00000002"
            assert (
                client.patch(f"/api/items/{first['id']}", json={"ktru_code": code}).status_code
                == 200
            )
            assert client.get(f"/api/jobs/{job_id}/export?format=csv").status_code == 200
            if stage != "failure":
                client.post(f"/api/jobs/{job_id}/stop")
        finally:
            release.set()
        work.result(5)
        job = client.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == ("failed" if stage == "failure" else "stopped")
        saved = client.get(f"/api/jobs/{job_id}/items?limit=200").json()["items"]
        assert saved[0]["id"] == first["id"]
        assert saved[0]["ktru_code"] == code and saved[0]["status"] == "corrected"
        assert len({i["id"] for i in saved}) == len(saved)
        assert [i["row_number"] for i in saved] == sorted(i["row_number"] for i in saved)
        assert len(calls) == 2


def test_stop_waiting_for_model_cancels_queued_chunks_and_retries(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def handler(request):
        calls.append(request.url.path)
        entered.set()
        assert release.wait(5)
        return httpx.Response(503, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        monkeypatch.setattr(extractor, "_client", transport)
        monkeypatch.setattr(extractor, "llm_state", lambda: "on")
        monkeypatch.setattr(extractor, "PROVIDER", "anthropic")
        monkeypatch.setattr(extractor, "_slots", threading.Semaphore(1))
        with ThreadPoolExecutor(4) as extraction_pool:
            monkeypatch.setattr(extractor, "_pool", extraction_pool)
            with TestClient(app.app) as client, ThreadPoolExecutor(1) as pool:
                job_id = upload(client)
                work = launch(app, client, job_id, pool)
                try:
                    assert entered.wait(5)
                    client.post(f"/api/jobs/{job_id}/stop")
                    work.result(3)
                    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "stopped"
                finally:
                    release.set()
        assert len(calls) == 1


def test_restart_keeps_existing_ids_and_manual_edits(app):
    with TestClient(app.app) as client:
        job_id = upload(client, 2)
        client.post(f"/api/jobs/{job_id}/start")
        claimed = app.claim_job()
        with app.SessionLocal() as session:
            item = app.build_item(claimed, parser.Item(1, "Бумага офисная А4 0", {}), {}, [])
            item.status, item.ktru_code = "corrected", "17.12.14.129-00000002"
            session.add(item)
            session.get(app.Job, claimed).processed = 1
            session.commit()
            stable_id = item.id
        app.run_job(claimed)
        items = client.get(f"/api/jobs/{job_id}/items").json()["items"]
        assert len(items) == 2
        assert items[0]["id"] == stable_id and items[0]["status"] == "corrected"
        assert client.get(f"/api/jobs/{job_id}").json()["processed"] == 2


def test_extractor_limits_pending_work_and_starts_chunks_in_source_order(monkeypatch):
    class Pool:
        def __init__(self):
            self.tasks = []

        def submit(self, function, *args):
            self.tasks.append((function, args))

    pool = Pool()
    seen = []
    monkeypatch.setattr(extractor, "_pool", pool)
    monkeypatch.setattr(extractor, "PARALLEL_REQUESTS", 2)
    monkeypatch.setattr(extractor, "BATCH_SIZE", 1)

    def extract(texts, deadline):
        seen.extend(texts)
        return [{"name": text} for text in texts]

    monkeypatch.setattr(extractor, "extract_chunk", extract)
    llm = extractor.JobExtractor([str(i) for i in range(20)])
    assert len(pool.tasks) == 2
    for function, args in pool.tasks:
        function(*args)
    assert seen == [str(i) for i in range(20)]
    assert llm.get("0")["name"] == "0"
    llm.close()


def test_multisheet_source_order_survives_resume(app, tmp_path):
    from openpyxl import Workbook

    book = Workbook()
    for sheet in (book.active, book.create_sheet()):
        sheet.append(["Наименование", "Количество"])
        sheet.append(["Бумага", 1])
        sheet.append(["Ручка", 2])
    path = tmp_path / "sheets.xlsx"
    book.save(path)
    rows = parser.parse_file(path)
    assert [row.row_number for row in rows] == [2, 3, 2, 3]
    assert [row.source_index for row in rows] == [1, 2, 3, 4]
    with TestClient(app.app) as client:
        job_id = client.post("/api/jobs", files={"file": (path.name, path.read_bytes())}).json()[
            "job_id"
        ]
        client.post(f"/api/jobs/{job_id}/start")
        claimed = app.claim_job()
        with app.SessionLocal() as session:
            first = app.build_item(claimed, rows[0], {}, [])
            session.add(first)
            session.get(app.Job, claimed).processed = 1
            session.commit()
            item_id = first.id
        edited = client.put(f"/api/items/{item_id}/attributes", json={"name": "Бумага для офиса"})
        assert edited.status_code == 200 and edited.json()["attributes"]["source_index"] == 1
        app.run_job(claimed)
        items = client.get(f"/api/jobs/{job_id}/items").json()["items"]
        assert [i["attributes"]["source_index"] for i in items] == [1, 2, 3, 4]
        assert [i["row_number"] for i in items] == [2, 3, 2, 3]
        assert items[0]["attributes"]["name"] == "Бумага для офиса"
