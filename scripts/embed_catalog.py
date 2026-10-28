"""Yandex embeddings of every catalog code -> an .npz for CATALOG_VECTORS (EMBED_PROVIDER=yandex).

Runs inside the backend container: it has the key, httpx and the embedder module.

    docker compose cp scripts/embed_catalog.py backend:/tmp/embed_catalog.py
    docker compose exec -d backend sh -c "python /tmp/embed_catalog.py \\
        /catalog/ktru_catalog.jsonl.gz /cache/ktru_vectors_yandex.npz > /cache/embed.log 2>&1"
    docker compose exec backend tail -3 /cache/embed.log
    docker compose cp backend:/cache/ktru_vectors_yandex.npz db/

The text of a code is its embedding_text from the dump (the bge-m3 vectors were built from it),
embedded by the doc model. Equal texts are sent once. About 77 600 requests at 10 a second:
a bit over two hours. The work done is saved every BLOCK texts to OUT.partial.npy; a new start
with the same arguments goes on from there.
"""

import argparse
import gzip
import json
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, "/app")
import embedder  # the backend module from the container

BLOCK = 1000
THREADS = 8  # requests in flight; the rate is held by embedder (YANDEX_EMBED_RPS)


def catalog_texts(path: pathlib.Path) -> tuple[list[str], list[str]]:
    """Codes in the order the search loads them (records with a vector) and their texts."""
    codes, texts = [], []
    with gzip.open(path, "rt", encoding="utf-8") as lines:
        for line in lines:
            record = json.loads(line)
            if not record.get("embedding_b64"):
                continue
            codes.append(record["code"])
            texts.append(record.get("embedding_text") or record.get("name") or record["code"])
    return codes, texts


def embed_one(text: str) -> np.ndarray:
    return embedder.yandex_vectors([text], embedder.YANDEX_DOC_MODEL, timeout=30.0)[0]


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("dump", type=pathlib.Path)
    args.add_argument("out", type=pathlib.Path)
    options = args.parse_args()
    if not embedder.YANDEX_KEY or not embedder.YANDEX_FOLDER:
        sys.exit("YANDEX_EMBED_API_KEY and YANDEX_EMBED_FOLDER_ID are needed")
    if embedder.DIM != 256:
        sys.exit("run with EMBED_PROVIDER=yandex")

    codes, texts = catalog_texts(options.dump)
    unique = list(dict.fromkeys(texts))
    partial = options.out.with_suffix(".partial.npy")
    done = np.load(partial) if partial.exists() else np.zeros((0, embedder.DIM), np.float32)
    print(f"{len(codes)} codes, {len(unique)} texts, {len(done)} done before", flush=True)

    started = time.monotonic()
    with ThreadPoolExecutor(THREADS) as pool:
        for start in range(len(done), len(unique), BLOCK):
            block = unique[start : start + BLOCK]
            vectors = np.stack(list(pool.map(embed_one, block)))
            done = np.concatenate([done, vectors])
            np.save(partial, done)
            elapsed = time.monotonic() - started
            print(f"{len(done)}/{len(unique)} texts, {elapsed:.0f} s", flush=True)

    row_of = {text: i for i, text in enumerate(unique)}
    matrix = done[[row_of[t] for t in texts]]
    np.savez(options.out, codes=np.asarray(codes), vectors=matrix.astype(np.float32))
    partial.unlink()
    print(f"saved {matrix.shape} to {options.out}", flush=True)


if __name__ == "__main__":
    main()
