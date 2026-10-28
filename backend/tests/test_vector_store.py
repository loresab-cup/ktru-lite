"""Exact pgvector retrieval agrees with in-memory cosine on controlled vectors."""

import os

import numpy as np
import pytest
from sqlalchemy import create_engine

import database as db
from migrate import upgrade_database
from vector_store import VectorStore, vectors


def test_pgvector_exact_neighbors(monkeypatch):
    url = os.getenv("MIGRATION_TEST_URL")
    if not url:
        pytest.skip("Dedicated pgvector server not configured")
    engine = create_engine(url)
    upgrade_database(engine)
    monkeypatch.setattr(db, "engine", engine)
    rng = np.random.default_rng(42)
    matrix = rng.normal(size=(70, 256)).astype(np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    from types import SimpleNamespace

    catalog = SimpleNamespace(
        vectors=matrix,
        codes=[str(i) for i in range(70)],
        statuses=["ACTIVE"] * 65 + ["INACTIVE"] * 5,
        active=np.arange(70) < 65,
    )
    store = VectorStore(catalog)
    try:
        store.sync()
        store.sync()
        for active in [True, False]:
            queries = matrix[[1, 20, 66]]
            found = store.nearest(queries, active, 5)
            for q, rows in zip(queries, found, strict=True):
                scores = matrix @ q
                if not active:
                    scores[~catalog.active] = -np.inf
                expected = np.argsort(-scores)[:5]
                assert [i for i, _ in rows] == list(expected)
                assert np.allclose([s for _, s in rows], scores[expected], atol=1e-6)
    finally:
        with engine.begin() as c:
            c.execute(vectors.delete().where(vectors.c.revision == store.revision))
        engine.dispose()
