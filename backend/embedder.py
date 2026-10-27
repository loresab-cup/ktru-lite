"""Texts -> bge-m3 vectors through Ollama (POST /api/embed), L2-normalized.

Ollama is down or answers garbage -> None: the search then works by words only. The last
answer is kept as the state for /api/health, so the page can say that the search is worse.
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
DIM = 1024
BATCH_SIZE = 64
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
    response = _client.post(
        f"{OLLAMA_URL}/api/embed", json={"model": MODEL, "input": texts}, timeout=timeout
    )
    response.raise_for_status()
    vectors = np.asarray(response.json()["embeddings"], dtype=np.float32)
    if vectors.shape != (len(texts), DIM):
        raise ValueError(f"unexpected embeddings shape {vectors.shape}")
    if not np.isfinite(vectors).all():
        raise ValueError("non-finite embeddings")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if (norms <= 1e-12).any():
        raise ValueError("zero embedding")
    return vectors / np.maximum(norms, 1e-12)


def embed(texts: list[str]) -> np.ndarray | None:
    """(len(texts), 1024) float32; equal texts are sent once and cached between jobs."""
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
            logger.warning("Ollama is unavailable, word search only: %s", type(exc).__name__)
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
