"""Yandex embeddings of every catalog code -> an .npz for CATALOG_VECTORS (EMBED_PROVIDER=yandex).

Runs inside the backend container: it has the key, httpx and the embedder module.

    docker compose cp scripts/embed_catalog.py backend:/tmp/embed_catalog.py
    docker compose exec -d backend sh -c "python /tmp/embed_catalog.py \\
        /catalog/ktru_catalog.jsonl.gz /cache/ktru_vectors_yandex.npz > /cache/embed.log 2>&1"
    docker compose exec backend tail -3 /cache/embed.log
    docker compose cp backend:/cache/ktru_vectors_yandex.npz db/

The text of a code is its embedding_text from the dump (the bge-m3 vectors were built from it),
embedded by the doc model. Equal texts are sent once: about 77 600 requests, a bit over two
hours at 10 a second. The quota is per cloud, so a key of another cloud adds its own: --accounts
takes the names of env variables (key:folder), e.g.
--accounts YANDEX_EMBED_API_KEY:YANDEX_EMBED_FOLDER_ID,YANDEX_API_KEY:YANDEX_FOLDER_ID
(the same model gives the same vectors in every cloud). The work done is saved every BLOCK
texts to OUT.partial.npy; a new start with the same arguments goes on from there.
"""

import argparse
import gzip
import json
import os
import pathlib
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

sys.path.insert(0, "/app")
import embedder  # the backend module from the container

BLOCK = 1000
THREADS = 8  # requests in flight per account; the rate is held by embedder (YANDEX_EMBED_RPS)
DEFAULT_ACCOUNTS = "YANDEX_EMBED_API_KEY:YANDEX_EMBED_FOLDER_ID"


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


def accounts_from_env(names: str) -> list[tuple[str, str]]:
    accounts = []
    for pair in names.split(","):
        key_name, folder_name = pair.split(":")
        key, folder = os.getenv(key_name, ""), os.getenv(folder_name, "")
        if not key or not folder:
            sys.exit(f"{key_name} and {folder_name} are needed")
        accounts.append((key, folder))
    return accounts


def embed_block(pool, block: list[str], accounts: list) -> np.ndarray:
    """Texts go to the accounts in turn: each one holds its own rate."""

    def one(i: int) -> np.ndarray:
        account = accounts[i % len(accounts)]
        model = embedder.YANDEX_DOC_MODEL
        return embedder.yandex_vectors([block[i]], model, timeout=30.0, account=account)[0]

    return np.stack(list(pool.map(one, range(len(block)))))


def main() -> None:
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("dump", type=pathlib.Path)
    args.add_argument("out", type=pathlib.Path)
    args.add_argument("--accounts", default=DEFAULT_ACCOUNTS)
    options = args.parse_args()
    accounts = accounts_from_env(options.accounts)
    if embedder.DIM != 256:
        sys.exit("run with EMBED_PROVIDER=yandex")

    codes, texts = catalog_texts(options.dump)
    unique = list(dict.fromkeys(texts))
    partial = options.out.with_suffix(".partial.npy")
    done = np.load(partial) if partial.exists() else np.zeros((0, embedder.DIM), np.float32)
    print(f"{len(codes)} codes, {len(unique)} texts, {len(done)} done before", flush=True)

    started = time.monotonic()
    with ThreadPoolExecutor(THREADS * len(accounts)) as pool:
        for start in range(len(done), len(unique), BLOCK):
            block = unique[start : start + BLOCK]
            vectors = embed_block(pool, block, accounts)
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
