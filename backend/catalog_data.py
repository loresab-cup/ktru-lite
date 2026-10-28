"""Read cached catalog metadata and validate its vector matrix."""

import gzip
import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


def catalog_vectors(path: Path, codes: list[str]) -> np.ndarray:
    """Vectors of the file in the order of the catalog codes, L2-normalized.

    A code of the catalog without a vector is an error: the file belongs to another dump."""
    with np.load(path) as data:
        stored = [str(c) for c in data["codes"]]
        vectors = np.asarray(data["vectors"], dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[0] != len(stored) or vectors.shape[1] != 256:
        raise ValueError(f"{path}: invalid catalog vector shape {vectors.shape}")
    if len(set(stored)) != len(stored) or not np.isfinite(vectors).all():
        raise ValueError(f"{path}: duplicate codes or non-finite catalog vectors")
    if stored != codes:
        index = {code: i for i, code in enumerate(stored)}
        missing = [c for c in codes if c not in index]
        if missing:
            raise ValueError(f"{path}: no vectors for {len(missing)} codes, e.g. {missing[0]}")
        vectors = vectors[[index[c] for c in codes]]
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    if (norms <= 1e-12).any():
        raise ValueError(f"{path}: zero catalog vector")
    return vectors / np.maximum(norms, 1e-12)


def load_data(path: Path, cache_dir: Path, vectors_file: str, builder, version: int):
    stat = path.stat()
    key = f"v{version}-{stat.st_size}-{stat.st_mtime_ns}"
    meta_path = cache_dir / f"catalog-{key}.json.gz"
    if not Path(vectors_file).is_file():  # fails at once, not after the 20 s build
        raise FileNotFoundError(f"no catalog vectors file {vectors_file}: see README")
    if meta_path.exists():
        with gzip.open(meta_path, "rt", encoding="utf-8") as file:
            meta = json.load(file)
        logger.info("catalog from cache: %s codes", len(meta["codes"]))
    else:
        meta = builder(path)
        logger.info("catalog built from the dump: %s codes", len(meta["codes"]))
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            for old in cache_dir.glob("catalog-*"):
                old.unlink()
            with gzip.open(meta_path, "wt", encoding="utf-8", compresslevel=1) as file:
                json.dump(meta, file, ensure_ascii=False)
        except OSError:
            logger.warning("catalog cache is not written to %s", cache_dir, exc_info=True)
    vectors = catalog_vectors(Path(vectors_file), meta["codes"])
    logger.info("catalog vectors from %s: %s numbers", vectors_file, vectors.shape[1])
    return vectors, meta
