"""KTRU catalog in memory and the search of candidate codes.

The dump (JSONL in gzip) is streamed once into an (n x 1024) float32 matrix; the matrix and
the short metadata are cached in CACHE_DIR, so later starts take seconds.
Search: exact cosine (queries @ matrix.T) -> 50 nearest -> + WORD_BONUS per shared word
(name + characteristic values: "А4", "80 г/м2" pick the code inside a group) -> top-5.
Exact, not HNSW: on this catalog HNSW loses up to 14 % of the right codes.
"""

import base64
import gzip
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

CATALOG_PATH = Path(os.getenv("CATALOG_PATH", "/catalog/ktru_catalog.jsonl.gz"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", "/cache"))
DIM = 1024
VECTOR_LIMIT = 50
WORD_BONUS = 0.02
TOP_K = 5
QUERY_CHUNK = 128  # 128 x 81 509 float32 = 42 MB of similarities at a time
WORDS_ONLY_BASE = 0.6  # without vectors the score stays low: nothing goes to auto
MAX_CODE_WORDS = 400
BRIEF_CHARS, BRIEF_VALUES = 12, 20

# Latin letters that look like Cyrillic ones: "A4" and "А4" must be the same word
_LOOKALIKE = str.maketrans("aceopxkmhbty", "асеорхкмнвту")
_WORD = re.compile(r"[a-zа-я0-9]+")


def words(text: str) -> set[str]:
    """Words for matching: 3+ letters or with a digit, lowercase, ё=е, Latin lookalikes."""
    text = (text or "").lower().replace("ё", "е").translate(_LOOKALIKE)
    return {w for w in _WORD.findall(text) if len(w) >= 3 or any(c.isdigit() for c in w)}


@dataclass
class Catalog:
    vectors: np.ndarray  # (n, DIM) float32, L2-normalized
    codes: list[str]
    statuses: list[str]
    names: list[str]
    okpd2: list[str]
    code_words: list[str]  # space-joined words of name + characteristic values
    chars: list[list]  # [[name, [values]]] short, for the UI
    index: dict[str, int] = field(init=False)
    active: np.ndarray = field(init=False)

    def __post_init__(self) -> None:
        self.index = {code: i for i, code in enumerate(self.codes)}
        self.active = np.array([s == "ACTIVE" for s in self.statuses], dtype=bool)
        self._names_lower = [name.lower().replace("ё", "е") for name in self.names]
        self._postings: dict[str, np.ndarray] | None = None
        self._lock = threading.Lock()

    # --- search ----------------------------------------------------------------------------

    def search(
        self, vectors: np.ndarray | None, texts: list[str], include_inactive: bool = False
    ) -> list[list[dict]]:
        """Top-5 candidates for every text; vectors=None -> by words only."""
        query_words = [words(text) for text in texts]
        if vectors is not None:
            nearest = self._nearest_by_vectors(vectors, include_inactive)
        else:
            nearest = [self._nearest_by_words(qw, include_inactive) for qw in query_words]
        return [self._rank(qw, near) for qw, near in zip(query_words, nearest, strict=True)]

    def _nearest_by_vectors(self, queries: np.ndarray, include_inactive: bool) -> list[list]:
        limit = min(VECTOR_LIMIT, len(self.codes))
        result = []
        for start in range(0, len(queries), QUERY_CHUNK):
            sims = queries[start : start + QUERY_CHUNK] @ self.vectors.T
            if not include_inactive:
                sims[:, ~self.active] = -np.inf
            top = np.argpartition(-sims, limit - 1, axis=1)[:, :limit]
            for row, idx in zip(sims, top, strict=True):
                result.append([(int(i), float(row[i])) for i in idx if np.isfinite(row[i])])
        return result

    def _nearest_by_words(self, query_words: set[str], include_inactive: bool) -> list:
        postings = self._name_postings()
        counts = np.zeros(len(self.codes), dtype=np.int32)
        for word in query_words:
            if word in postings:
                counts[postings[word]] += 1
        if not include_inactive:
            counts[~self.active] = 0
        top = np.argsort(-counts, kind="stable")[:VECTOR_LIMIT]
        total = max(1, len(query_words))
        return [(int(i), WORDS_ONLY_BASE * counts[i] / total) for i in top if counts[i] > 0]

    def _name_postings(self) -> dict[str, np.ndarray]:
        """word -> indices of codes with it in the name; built on the first words-only search."""
        with self._lock:
            if self._postings is None:
                lists: dict[str, list[int]] = {}
                for i, name in enumerate(self.names):
                    for word in words(name):
                        lists.setdefault(word, []).append(i)
                self._postings = {w: np.array(ix, dtype=np.int32) for w, ix in lists.items()}
            return self._postings

    def _rank(self, query_words: set[str], nearest: list[tuple[int, float]]) -> list[dict]:
        scored = []
        for i, cosine in nearest:
            shared = query_words & set(self.code_words[i].split())
            scored.append((cosine + WORD_BONUS * len(shared), cosine, i, shared))
        # Sorted by the raw sum: the cap at 1.0 must not make near-perfect matches equal
        scored.sort(key=lambda row: row[0], reverse=True)
        return [
            {
                **self.brief(i),
                "score": round(min(1.0, score), 4),
                "cosine": round(cosine, 4),
                "shared_words": sorted(shared)[:12],
                "matched_characteristics": self._matched_chars(i, query_words),
            }
            for score, cosine, i, shared in scored[:TOP_K]
        ]

    def _matched_chars(self, i: int, query_words: set[str]) -> list[str]:
        matched = []
        for name, values in self.chars[i]:
            for value in values:
                value_words = words(value)
                if value_words and value_words <= query_words:
                    matched.append(f"{name}: {value}")
                    break
        return matched[:6]

    # --- lookup ----------------------------------------------------------------------------

    def brief(self, i: int) -> dict:
        return {
            "code": self.codes[i],
            "name": self.names[i],
            "okpd2_code": self.okpd2[i],
            "status": self.statuses[i],
            "characteristics": [[name, values[:5]] for name, values in self.chars[i][:6]],
        }

    def get(self, code: str) -> dict | None:
        i = self.index.get(code.strip())
        return None if i is None else self.brief(i)

    def lookup(self, query: str, limit: int = 20) -> list[dict]:
        """Manual correction: by code prefix or by words of the name; ACTIVE codes first."""
        query = query.strip().lower().replace("ё", "е")
        if not query:
            return []
        if re.fullmatch(r"[\d.\-]+", query):
            found = [i for i, code in enumerate(self.codes) if code.startswith(query)]
        else:
            parts = query.split()
            found = [i for i, n in enumerate(self._names_lower) if all(p in n for p in parts)]
        found.sort(key=lambda i: (not self.active[i], len(self.names[i]), self.codes[i]))
        return [self.brief(i) for i in found[:limit]]


# --- loading -------------------------------------------------------------------------------


def brief_chars(characteristics: list) -> tuple[list, list[str]]:
    """Short characteristics for the UI and all their value texts for word matching."""
    brief, texts = [], []
    for characteristic in characteristics or []:
        values = [v.get("text") for v in characteristic.get("values") or [] if v.get("text")]
        texts.extend(values)
        if len(brief) < BRIEF_CHARS:
            brief.append(
                [characteristic.get("name") or "", [v[:80] for v in values[:BRIEF_VALUES]]]
            )
    return brief, texts


def build(path: Path) -> tuple[np.ndarray, dict]:
    """Streams the dump: vectors into a growing matrix, the rest into short lists."""
    vectors = np.empty((90_000, DIM), dtype=np.float32)
    meta: dict[str, list] = {
        k: [] for k in ("codes", "statuses", "names", "okpd2", "words", "chars")
    }
    n = 0
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            record = json.loads(line)
            if not record.get("embedding_b64"):
                continue
            if n == len(vectors):
                vectors = np.concatenate([vectors, np.empty((20_000, DIM), np.float32)])
            vectors[n] = np.frombuffer(base64.b64decode(record["embedding_b64"]), dtype="<f4")
            chars, value_texts = brief_chars(record.get("characteristics"))
            code_words = list(words(" ".join([record.get("name") or "", *value_texts])))
            meta["codes"].append(record["code"])
            meta["statuses"].append(record.get("status") or "ACTIVE")
            meta["names"].append(record.get("name") or "")
            meta["okpd2"].append(record.get("okpd2_code") or "")
            meta["words"].append(" ".join(code_words[:MAX_CODE_WORDS]))
            meta["chars"].append(chars)
            n += 1
    return vectors[:n], meta  # a view: no second 334 MB copy


def load_catalog(path: Path | None = None, cache_dir: Path | None = None) -> Catalog:
    path, cache_dir = path or CATALOG_PATH, cache_dir or CACHE_DIR
    stat = path.stat()
    key = f"{stat.st_size}-{stat.st_mtime_ns}"
    vectors_path = cache_dir / f"catalog-{key}.npy"
    meta_path = cache_dir / f"catalog-{key}.json.gz"
    if vectors_path.exists() and meta_path.exists():
        vectors = np.load(vectors_path)
        with gzip.open(meta_path, "rt", encoding="utf-8") as file:
            meta = json.load(file)
        logger.info("catalog from cache: %s codes", len(meta["codes"]))
    else:
        vectors, meta = build(path)
        logger.info("catalog built from the dump: %s codes", len(meta["codes"]))
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            for old in cache_dir.glob("catalog-*"):
                old.unlink()
            np.save(vectors_path, vectors)
            with gzip.open(meta_path, "wt", encoding="utf-8", compresslevel=1) as file:
                json.dump(meta, file, ensure_ascii=False)
        except OSError:
            logger.warning("catalog cache is not written to %s", cache_dir, exc_info=True)
    return Catalog(
        vectors=vectors,
        codes=meta["codes"],
        statuses=meta["statuses"],
        names=meta["names"],
        okpd2=meta["okpd2"],
        code_words=meta["words"],
        chars=meta["chars"],
    )
