"""Offline replay must never fall back to paid embeddings or invent a selected-code score."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import confidence
import parser
import pipeline
import search


@pytest.fixture
def replay_script():
    path = Path(__file__).resolve().parents[2] / "scripts/replay.py"
    spec = importlib.util.spec_from_file_location("replay_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_embeddings_refuse_missing_queries(replay_script, tmp_path):
    path = tmp_path / "queries.npz"
    np.savez(path, queries=np.array(["Paper"]), vectors=np.ones((1, 256)))
    frozen = replay_script.FrozenEmbeddings(path)
    assert frozen.embed(["Paper"]).shape == (1, 256)
    with pytest.raises(KeyError):
        frozen.embed(["Unknown"])
    assert replay_script.FrozenEmbeddings(None).embed(["Anything"]) is None


def test_replay_preserves_customer_unit_over_llm_guess(replay_script):
    attrs = {"measure": "усл. ед"}
    row = parser.Item(1, "Услуги телефонной связи", {"Ед. изм.": "Минута"})
    assert replay_script.unit_hints(search, row.columns, attrs) == pipeline.unit_hints(row, attrs)


def test_replay_uses_confidence_of_selected_second_candidate(replay_script):
    candidates = [
        {"code": "A", "name": "Paper", "score": 0.9, "status": "ACTIVE"},
        {"code": "B", "name": "Paper", "score": 0.89, "status": "ACTIVE"},
    ]
    item = {
        "id": 1,
        "raw_text": "Paper",
        "attributes": {"name": "Paper", "search_text": "Paper"},
        "candidates": candidates,
        "summary": "Код выбран нейросетью",
        "ktru_code": "B",
    }
    catalog = SimpleNamespace(search=lambda *args, **kwargs: [candidates])
    [result] = replay_script.replay(
        catalog, replay_script.FrozenEmbeddings(None), search, confidence, [item]
    )
    assert result["ktru_code"] == "B"
    assert result["confidence"] == confidence.for_code(candidates, "B")
    assert result["status"] == "needs_review"
