"""Yandex embeddings instead of bge-m3: requests, retries and the catalog vectors file."""

import base64
import gzip
import json

import httpx
import numpy as np
import pytest

import embedder
import search


@pytest.fixture
def yandex(monkeypatch):
    """embedder in the Yandex mode with a fake gateway; returns the list of request bodies."""
    sent = []
    answers = []  # status codes to answer before a good one

    def handler(request):
        body = json.loads(request.content)
        sent.append((body, request.headers))
        if answers:
            return httpx.Response(answers.pop(0))
        seed = sum(map(ord, body["text"])) % 7 + 1
        return httpx.Response(200, json={"embedding": [float(seed)] * 256, "numTokens": "3"})

    monkeypatch.setattr(embedder, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(embedder, "_cache", {})
    monkeypatch.setattr(embedder, "_status", {"up": None, "at": 0.0, "pause_until": 0.0})
    monkeypatch.setattr(embedder, "YANDEX", True)
    monkeypatch.setattr(embedder, "DIM", 256)
    monkeypatch.setattr(embedder, "YANDEX_KEY", "test-key")
    monkeypatch.setattr(embedder, "YANDEX_FOLDER", "b1gfolder")
    monkeypatch.setattr(embedder, "YANDEX_RPS", 1000.0)
    monkeypatch.setattr(embedder.time, "sleep", lambda _: None)
    return sent, answers


def test_rows_go_to_the_query_model_one_text_per_request(yandex):
    sent, _ = yandex
    vectors = embedder.embed(["Бумага А4", "Лампа", "Бумага А4"])
    assert vectors.shape == (3, 256)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0)
    assert sorted(body["text"] for body, _ in sent) == ["Бумага А4", "Лампа"]  # equal: once
    body, headers = sent[0]
    assert body["modelUri"] == "emb://b1gfolder/text-search-query/latest"
    assert headers["authorization"] == "Api-Key test-key"
    assert headers["x-folder-id"] == "b1gfolder"


def test_catalog_texts_go_to_the_doc_model(yandex):
    sent, _ = yandex
    embedder.yandex_vectors(["Бумага для офисной техники"], embedder.YANDEX_DOC_MODEL)
    assert sent[0][0]["modelUri"] == "emb://b1gfolder/text-search-doc/latest"


def test_too_many_requests_are_retried(yandex):
    sent, answers = yandex
    answers.extend([429, 503])
    assert embedder.embed(["Лампа"]).shape == (1, 256)
    assert len(sent) == 3


def test_a_bad_key_gives_words_only_without_retries(yandex):
    sent, answers = yandex
    answers.extend([401] * 10)
    assert embedder.embed(["Лампа"]) is None
    assert len(sent) == 1
    assert embedder._status["up"] is False


def test_without_a_key_nothing_is_sent(yandex, monkeypatch):
    sent, _ = yandex
    monkeypatch.setattr(embedder, "YANDEX_KEY", "")
    assert embedder.embed(["Лампа"]) is None
    assert sent == []


def catalog_dump(path, codes):
    with gzip.open(path, "wt", encoding="utf-8") as file:
        for code in codes:
            vector = np.zeros(search.DIM, np.float32)
            record = {"code": code, "name": code, "characteristics": [], "units": []}
            record["embedding_b64"] = base64.b64encode(vector.tobytes()).decode()
            file.write(json.dumps(record) + "\n")


def test_catalog_takes_the_vectors_of_the_file_by_code(tmp_path):
    codes = ["01.11.10.000-00000001", "01.11.10.000-00000002", "01.11.10.000-00000003"]
    catalog_dump(tmp_path / "dump.jsonl.gz", codes)
    vectors = np.eye(3, 256, dtype=np.float32) * 2  # not normalized on purpose
    file = tmp_path / "vectors.npz"
    np.savez(file, codes=np.asarray(codes[::-1]), vectors=vectors)  # another order

    catalog = search.load_catalog(tmp_path / "dump.jsonl.gz", tmp_path / "cache", str(file))
    assert catalog.vectors.shape == (3, 256)
    assert np.allclose(catalog.vectors[0], np.eye(3, 256)[2])  # the vector of codes[0]
    again = search.load_catalog(tmp_path / "dump.jsonl.gz", tmp_path / "cache", str(file))
    assert np.allclose(again.vectors, catalog.vectors)  # from the cache, same vectors


def test_vectors_of_another_dump_are_an_error(tmp_path):
    catalog_dump(tmp_path / "dump.jsonl.gz", ["01.11.10.000-00000001", "01.11.10.000-00000002"])
    file = tmp_path / "vectors.npz"
    np.savez(file, codes=np.asarray(["01.11.10.000-00000001"]), vectors=np.ones((1, 256)))
    with pytest.raises(ValueError, match="no vectors for 1 codes"):
        search.load_catalog(tmp_path / "dump.jsonl.gz", tmp_path / "cache", str(file))
