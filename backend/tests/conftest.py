"""The API tests of the pipeline run without accounts; tests/test_auth.py turns them on."""

import os

os.environ.setdefault("AUTH_REQUIRED", "0")

import importlib
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def api_database(monkeypatch, tmp_path):
    """Isolated API database with explicit lifecycle control and no external LLM calls."""
    import auth
    import database
    import extractor
    import job_queue
    import models
    import runtime

    api = importlib.import_module("api")
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    models.Base.metadata.create_all(engine)
    auth.AuthBase.metadata.create_all(engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(runtime, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(extractor, "PROVIDERS", [])
    monkeypatch.setattr(job_queue, "publish", lambda job_id: True)

    @asynccontextmanager
    async def lifespan(_):
        yield

    monkeypatch.setattr(api.app.router, "lifespan_context", lifespan)
    try:
        yield api
    finally:
        engine.dispose()
