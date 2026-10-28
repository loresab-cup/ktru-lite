"""Texts -> L2-normalized vectors of Yandex AI Studio (256 numbers, one text per request).

Rows go to the query model, the catalog was embedded by the doc model into CATALOG_VECTORS
(scripts/embed_catalog.py). No key, the service is down or answers garbage -> None: the search
then works by words only. The last answer is kept as the state for /api/health, so the page can say that the search
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
DIM = 256
# One text per request: small chunks let the pool keep several requests in flight
BATCH_SIZE = 4
PARALLEL = 4
CACHE_LIMIT = 50_000
# /api/health asks the service itself at most this often; the jobs report every answer meanwhile
CHECK_EVERY = 30.0
PROBE_TIMEOUT = 5.0
# A request that hung for the whole timeout: the next batches go by words at once for a while
# instead of each one waiting two minutes again. A refused connection fails at once, no pause.
HANG_PAUSE = 30.0
# A refused key (401, 403 - no role in the folder): every batch would ask again and fail the same
# way, so the next batches go by words for a while; the health probe asks again after it
REFUSED_PAUSE = 120.0
# One short text per request answers in ~0.1-0.3 s; a request still silent after this has hung
REQUEST_TIMEOUT = 20.0

_pool = ThreadPoolExecutor(PARALLEL, thread_name_prefix="embed")
_client = httpx.Client(timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=3.0))
_cache: dict[str, np.ndarray] = {}
_lock = threading.Lock()
_probe_lock = threading.Lock()
_status = {"up": None, "at": 0.0, "pause_until": 0.0}  # up: None - not asked yet
_rate_lock = threading.Lock()
_next_slot: dict[str, float] = {}  # folder -> monotonic time of its next allowed request


def _mark(up: bool, error: Exception | None = None) -> None:
    now = time.monotonic()
    _status.update(up=up, at=now, pause_until=now + _pause_for(error) if error else 0.0)


def _pause_for(error: Exception) -> float:
    """Seconds the batches go by words without asking; 0 for a refused connection or garbage."""
    if isinstance(error, httpx.TimeoutException):
        return HANG_PAUSE
    if isinstance(error, httpx.HTTPStatusError) and error.response.status_code in (401, 403):
        return REFUSED_PAUSE
    return 0.0


def state() -> str:
    """on / off / down / unknown; the service is asked again when the last answer is older than
    CHECK_EVERY. off: no key, nothing is asked.

    Only one caller probes at a time, the others get the last known state at once."""
    if not YANDEX_KEY or not YANDEX_FOLDER:
        return "off"
    stale = time.monotonic() - _status["at"] > CHECK_EVERY
    if stale and _probe_lock.acquire(blocking=False):
        try:
            _request(["проверка"], timeout=PROBE_TIMEOUT)
            _mark(True)
        except Exception as exc:  # noqa: BLE001 - any failure means "down"
            _mark(False, exc)
        finally:
            _probe_lock.release()
    return {None: "unknown", True: "on", False: "down"}[_status["up"]]


def _request(texts: list[str], timeout=httpx.USE_CLIENT_DEFAULT) -> np.ndarray:
    return yandex_vectors(texts, YANDEX_QUERY_MODEL, timeout)


def _wait_turn(folder: str) -> None:
    """Spreads the requests of all threads to one folder to YANDEX_RPS a second."""
    with _rate_lock:
        now = time.monotonic()
        start = max(now, _next_slot.get(folder, 0.0))
        _next_slot[folder] = start + 1.0 / YANDEX_RPS
    if start > now:
        time.sleep(start - now)


def _yandex_one(text: str, model: str, timeout, account: tuple[str, str] | None) -> list[float]:
    key, folder = account or (YANDEX_KEY, YANDEX_FOLDER)
    if not key or not folder:
        raise RuntimeError("YANDEX_EMBED_API_KEY or YANDEX_EMBED_FOLDER_ID is not set")
    body = {"modelUri": f"emb://{folder}/{model}/latest", "text": text[:YANDEX_MAX_CHARS]}
    headers = {"Authorization": f"Api-Key {key}", "x-folder-id": folder}
    for attempt in range(YANDEX_RETRIES + 1):
        _wait_turn(folder)
        response = _client.post(YANDEX_URL, json=body, headers=headers, timeout=timeout)
        retry = response.status_code == 429 or response.status_code >= 500
        if not retry or attempt == YANDEX_RETRIES:
            break
        time.sleep(0.5 * 2**attempt)
    response.raise_for_status()
    return response.json()["embedding"]


def yandex_vectors(
    texts: list[str],
    model: str,
    timeout=httpx.USE_CLIENT_DEFAULT,
    account: tuple[str, str] | None = None,
) -> np.ndarray:
    """One request per text, in order; any failure raises.

    account = (key, folder) of another cloud: scripts/embed_catalog.py adds its quota."""
    vectors = [_yandex_one(t or " ", model, timeout, account) for t in texts]
    return _normalized(vectors, len(texts))


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
        if not YANDEX_KEY or not YANDEX_FOLDER:
            return None  # no key: words only, /api/health says "off"
        if time.monotonic() < _status["pause_until"]:
            return None  # hung or refused a moment ago: not asking again yet
        chunks = [missing[i : i + BATCH_SIZE] for i in range(0, len(missing), BATCH_SIZE)]
        try:
            results = list(_pool.map(_request, chunks))
        except Exception as exc:  # noqa: BLE001 - any failure means "no vectors"
            logger.warning("embeddings are unavailable, word search only: %s", type(exc).__name__)
            _mark(False, exc)
            return None
        _mark(True)
        for chunk, vectors in zip(chunks, results, strict=True):
            found.update(zip(chunk, vectors, strict=True))
        with _lock:
            if len(_cache) > CACHE_LIMIT:
                _cache.clear()
            _cache.update((t, found[t]) for t in missing)
    return np.stack([found[t] for t in texts])
