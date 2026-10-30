"""Catalog startup recovers after missing files become available."""

from types import SimpleNamespace

import numpy as np

import runtime as rt
import workers


def test_catalog_retries_until_available(monkeypatch):
    monkeypatch.setattr(rt.state, "catalog", None)
    attempts = []

    def load():
        attempts.append(1)
        if len(attempts) == 2:
            rt.state.catalog = object()

    waits = []
    stop = SimpleNamespace(
        is_set=lambda: False, wait=lambda seconds: waits.append(seconds) or False
    )
    monkeypatch.setattr(workers, "load_catalog", load)
    workers.load_catalog_when_available(stop)
    assert len(attempts) == 2 and waits == [15]


def test_catalog_retry_stops_on_shutdown(monkeypatch):
    monkeypatch.setattr(rt.state, "catalog", None)
    attempts = []
    monkeypatch.setattr(workers, "load_catalog", lambda: attempts.append(1))
    stop = SimpleNamespace(is_set=lambda: False, wait=lambda seconds: True)
    workers.load_catalog_when_available(stop)
    assert len(attempts) == 1


def test_successful_catalog_load_clears_old_error(monkeypatch):
    catalog = SimpleNamespace(vectors=np.zeros((1, workers.embedder.DIM)))
    monkeypatch.setattr(rt.state, "catalog", None)
    monkeypatch.setattr(rt.state, "catalog_error", "FileNotFoundError")
    monkeypatch.setattr(workers.search, "load_catalog", lambda: catalog)
    monkeypatch.setattr(
        workers.db, "engine", SimpleNamespace(dialect=SimpleNamespace(name="sqlite"))
    )
    workers.load_catalog()
    assert rt.state.catalog is catalog and rt.state.catalog_error is None


def test_api_reports_missing_catalog_without_blocking_queued_uploads(api_database, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(rt.state, "catalog", None)
    monkeypatch.setattr(rt.state, "catalog_error", "Catalog files are missing")
    with TestClient(api_database.app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        assert health.json()["catalog"] == "error"
        assert client.get("/api/catalog/search", params={"q": "Бумага"}).status_code == 503
        uploaded = client.post("/api/jobs", files={"file": ("paper.txt", "Бумага А4".encode())})
        assert uploaded.status_code == 202
        job = client.get("/api/jobs/" + uploaded.json()["job_id"]).json()
        assert job["status"] == "queued"
