"""Texts -> bge-m3 vectors through Ollama (POST /api/embed), L2-normalized.

Ollama is down or answers garbage -> None: the search then works by words only.
"""

import logging
import os
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor

import httpx
import numpy as np

logger = logging.getLogger(__name__)

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
MODEL = os.getenv("EMBED_MODEL", "bge-m3")
DIM = 1024
BATCH_SIZE = 64
PARALLEL = 4
CACHE_LIMIT = 50_000

_pool = ThreadPoolExecutor(PARALLEL, thread_name_prefix="embed")
_client = httpx.Client(timeout=httpx.Timeout(120.0, connect=3.0))
_cache: dict[str, np.ndarray] = {}
_lock = threading.Lock()


def _request(texts: list[str]) -> np.ndarray:
    response = _client.post(f"{OLLAMA_URL}/api/embed", json={"model": MODEL, "input": texts})
    response.raise_for_status()
    vectors = np.asarray(response.json()["embeddings"], dtype=np.float32)
    if vectors.shape != (len(texts), DIM):
        raise ValueError(f"unexpected embeddings shape {vectors.shape}")
    if not np.isfinite(vectors).all():
        raise ValueError("non-finite embeddings")
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if not np.isfinite(norms).all() or (norms <= 1e-12).any():
        raise ValueError("invalid embedding norms")
    return vectors / np.maximum(norms, 1e-12)


def embed(texts: list[str], cancel: threading.Event | None = None) -> np.ndarray | None:
    """(len(texts), 1024) float32; equal texts are sent once and cached between jobs."""
    if not texts:
        return np.zeros((0, DIM), np.float32)
    with _lock:
        found = {t: _cache[t] for t in texts if t in _cache}
    missing = list(dict.fromkeys(t for t in texts if t not in found))
    if missing:
        chunks = [missing[i : i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
        try:

            def request_chunk(chunk):
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
                return _request(chunk)

            results = list(_pool.map(request_chunk, chunks))
        except CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure means "no vectors"
            logger.warning("Ollama is unavailable, word search only: %s", type(exc).__name__)
            return None
        for chunk, vectors in zip(chunks, results, strict=True):
            found.update(zip(chunk, vectors, strict=True))
        with _lock:
            if len(_cache) > CACHE_LIMIT:
                _cache.clear()
            _cache.update((t, found[t]) for t in missing)
    return np.stack([found[t] for t in texts])
