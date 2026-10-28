"""Texts -> L2-normalized vectors: bge-m3 through Ollama (POST /api/embed) or, without a GPU,
Yandex AI Studio text embeddings (EMBED_PROVIDER=yandex, 256 numbers, one text per request).

The catalog must be embedded by the same model: the bge-m3 vectors are in the dump, the Yandex
ones in CATALOG_VECTORS (scripts/embed_catalog.py builds them with the doc model; rows go to the
query model). The service is down or answers garbage -> None: the search then works by words
only. The last answer is kept as the state for /api/health, so the page can say that the search
is worse.
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import numpy as np

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
MODEL = os.getenv("EMBED_MODEL", "bge-m3")
PROVIDER = os.getenv("EMBED_PROVIDER", "ollama").strip().lower()  # ollama | yandex
YANDEX_URL = os.getenv(
    "YANDEX_EMBED_URL", "https://llm.api.cloud.yandex.net/foundationModels/v1/textEmbedding"
)
YANDEX_KEY = os.getenv("YANDEX_EMBED_API_KEY", "")
YANDEX_FOLDER = os.getenv("YANDEX_EMBED_FOLDER_ID", "")
YANDEX_QUERY_MODEL = os.getenv("YANDEX_EMBED_QUERY_MODEL", "text-search-query")
YANDEX_DOC_MODEL = "text-search-doc"
# The cloud allows 10 embedding requests a second (all keys of the cloud together); more -> 429
YANDEX_RPS = float(os.getenv("YANDEX_EMBED_RPS", "10"))
YANDEX_RETRIES = 4  # 429 and 5xx: 0.5 + 1 + 2 + 4 s at most, then the batch goes by words
YANDEX_MAX_CHARS = 4000  # the model reads at most 2048 tokens
YANDEX = PROVIDER == "yandex"
DIM = 256 if YANDEX else 1024
# Yandex takes one text per request: small chunks let the pool keep several requests in flight
BATCH_SIZE = 4 if YANDEX else 64
PARALLEL = 4
CACHE_LIMIT = 50_000
# /api/health asks Ollama itself at most this often; the jobs report every answer meanwhile
CHECK_EVERY = 30.0
PROBE_TIMEOUT = 5.0
# A request that hung for the whole timeout: the next batches go by words at once for a while
# instead of each one waiting two minutes again. A refused connection fails at once, no pause.
HANG_PAUSE = 30.0

_pool = ThreadPoolExecutor(PARALLEL, thread_name_prefix="embed")
_client = httpx.Client(timeout=httpx.Timeout(120.0, connect=3.0))
_cache: dict[str, np.ndarray] = {}
_lock = threading.Lock()
_probe_lock = threading.Lock()
_status = {"up": None, "at": 0.0, "pause_until": 0.0}  # up: None - not asked yet
_rate_lock = threading.Lock()
_next_slot = [0.0]  # monotonic time of the next allowed Yandex request


def _mark(up: bool, hung: bool = False) -> None:
    now = time.monotonic()
    _status.update(up=up, at=now, pause_until=now + HANG_PAUSE if hung else 0.0)


def state() -> str:
    """on / down / unknown; Ollama is asked again when the last answer is older than CHECK_EVERY.

    Only one caller probes at a time, the others get the last known state at once."""
    stale = time.monotonic() - _status["at"] > CHECK_EVERY
    if stale and _probe_lock.acquire(blocking=False):
        try:
            _request(["проверка"], timeout=PROBE_TIMEOUT)
            _mark(True)
        except Exception as exc:  # noqa: BLE001 - any failure means "down"
            _mark(False, hung=isinstance(exc, httpx.TimeoutException))
        finally:
            _probe_lock.release()
    return {None: "unknown", True: "on", False: "down"}[_status["up"]]


def _request(texts: list[str], timeout=httpx.USE_CLIENT_DEFAULT) -> np.ndarray:
    if YANDEX:
        return yandex_vectors(texts, YANDEX_QUERY_MODEL, timeout)
    response = _client.post(
        f"{OLLAMA_URL}/api/embed", json={"model": MODEL, "input": texts}, timeout=timeout
    )
    response.raise_for_status()
    return _normalized(response.json()["embeddings"], len(texts))


def _wait_turn() -> None:
    """Spreads the requests of all threads to YANDEX_RPS a second."""
    with _rate_lock:
        now = time.monotonic()
        start = max(now, _next_slot[0])
        _next_slot[0] = start + 1.0 / YANDEX_RPS
    if start > now:
        time.sleep(start - now)


def _yandex_one(text: str, model: str, timeout) -> list[float]:
    if not YANDEX_KEY or not YANDEX_FOLDER:
        raise RuntimeError("YANDEX_EMBED_API_KEY or YANDEX_EMBED_FOLDER_ID is not set")
    body = {"modelUri": f"emb://{YANDEX_FOLDER}/{model}/latest", "text": text[:YANDEX_MAX_CHARS]}
    headers = {"Authorization": f"Api-Key {YANDEX_KEY}", "x-folder-id": YANDEX_FOLDER}
    for attempt in range(YANDEX_RETRIES + 1):
        _wait_turn()
        response = _client.post(YANDEX_URL, json=body, headers=headers, timeout=timeout)
        retry = response.status_code == 429 or response.status_code >= 500
        if not retry or attempt == YANDEX_RETRIES:
            break
        time.sleep(0.5 * 2**attempt)
    response.raise_for_status()
    return response.json()["embedding"]


def yandex_vectors(texts: list[str], model: str, timeout=httpx.USE_CLIENT_DEFAULT) -> np.ndarray:
    """One request per text, in order; any failure raises."""
    return _normalized([_yandex_one(t or " ", model, timeout) for t in texts], len(texts))


def _normalized(rows: list, count: int) -> np.ndarray:
    vectors = np.asarray(rows, dtype=np.float32)
    if vectors.shape != (count, DIM):
        raise ValueError(f"unexpected embeddings shape {vectors.shape}")
    if not np.isfinite(vectors).all():
        raise ValueError("non-finite embeddings")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if (norms <= 1e-12).any():
        raise ValueError("zero embedding")
    return vectors / np.maximum(norms, 1e-12)


def embed(texts: list[str]) -> np.ndarray | None:
    """(len(texts), DIM) float32; equal texts are sent once and cached between jobs."""
    if not texts:
        return np.zeros((0, DIM), np.float32)
    with _lock:
        found = {t: _cache[t] for t in texts if t in _cache}
    missing = list(dict.fromkeys(t for t in texts if t not in found))
    if missing:
        if time.monotonic() < _status["pause_until"]:
            return None  # hung a moment ago: not waiting for it again
        chunks = [missing[i : i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
        try:
            results = list(_pool.map(_request, chunks))
        except Exception as exc:  # noqa: BLE001 - any failure means "no vectors"
            logger.warning("%s is unavailable, word search only: %s", PROVIDER, type(exc).__name__)
            _mark(False, hung=isinstance(exc, httpx.TimeoutException))
            return None
        _mark(True)
        for chunk, vectors in zip(chunks, results, strict=True):
            found.update(zip(chunk, vectors, strict=True))
        with _lock:
            if len(_cache) > CACHE_LIMIT:
                _cache.clear()
            _cache.update((t, found[t]) for t in missing)
    return np.stack([found[t] for t in texts])
