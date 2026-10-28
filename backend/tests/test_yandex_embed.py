"""Yandex embeddings: requests, retries and the catalog vectors file."""

import gzip
import json

import httpx
import numpy as np
import pytest

import confidence
import embedder
import search


@pytest.fixture
def yandex(monkeypatch):
    """embedder with a key and a fake gateway; returns the list of request bodies."""
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


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_key_pauses_the_vectors(yandex, status):
    sent, answers = yandex
    answers.extend([status] * 10)
    assert embedder.embed(["Лампа"]) is None
    assert embedder.embed(["Стол"]) is None  # paused: no second request for every batch
    assert len(sent) == 1
    assert embedder._status["pause_until"] > embedder.time.monotonic() + 100


def test_a_broken_answer_does_not_pause(yandex):
    _, answers = yandex
    answers.append(400)
    assert embedder.embed(["Лампа"]) is None
    assert embedder.embed(["Стол"]).shape == (1, 256)  # the next batch asks again


def test_without_a_key_nothing_is_sent(yandex, monkeypatch):
    sent, _ = yandex
    monkeypatch.setattr(embedder, "YANDEX_KEY", "")
    assert embedder.embed(["Лампа"]) is None
    assert sent == []


def catalog_dump(path, codes):
    with gzip.open(path, "wt", encoding="utf-8") as file:
        for code in codes:
            record = {"code": code, "name": code, "characteristics": [], "units": []}
            record["embedding_b64"] = "AAAA"  # the old vector: only its presence counts
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


def test_no_vectors_file_is_a_clear_error(tmp_path):
    catalog_dump(tmp_path / "dump.jsonl.gz", ["01.11.10.000-00000001"])
    with pytest.raises(FileNotFoundError, match="no catalog vectors file"):
        search.load_catalog(tmp_path / "dump.jsonl.gz", tmp_path / "cache", str(tmp_path / "x"))
    assert not (tmp_path / "cache").exists()  # failed before the 20 s build


def test_the_confidence_fit_is_the_yandex_one():
    assert (confidence.W_SCORE, confidence.W_MARGIN, confidence.BIAS) == (0.80, 167.2, -1.38)
    # the cosine says little here: a clear margin goes to auto, a near tie does not
    assert confidence.probability(0.70, 0.02) >= confidence.HIGH
    assert confidence.probability(0.75, 0.002) < confidence.HIGH


def test_without_a_key_the_health_says_off(monkeypatch):
    monkeypatch.setattr(embedder, "YANDEX_KEY", "")
    assert embedder.state() == "off"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 0.0])
def test_corrupt_catalog_vectors_are_rejected(tmp_path, bad):
    file = tmp_path / "vectors.npz"
    np.savez(file, codes=np.asarray(["code"]), vectors=np.full((1, 256), bad))
    with pytest.raises(ValueError):
        search.catalog_vectors(file, ["code"])
