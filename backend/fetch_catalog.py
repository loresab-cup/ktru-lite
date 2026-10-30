"""The two catalog files for db/: downloaded from the release of the repository when missing.

    python fetch_catalog.py /catalog

Runs as the one-shot `catalog` service of docker-compose.yml before the backend, so a fresh clone
starts with one command. A file that is there with the right size is left alone; a downloaded one
must have the SHA-256 below. Any failure is reported and the exit code is still 0: the backend
then starts without the catalog and the site says what is missing.
"""

import hashlib
import os
import sys
from pathlib import Path

import httpx

RELEASE = os.getenv("CATALOG_RELEASE_URL") or (
    "https://github.com/loresab-cup/ktru-lite/releases/download/catalog_yandex"
)
FILES = {  # name -> (bytes, sha256) of the release catalog_yandex, 28.10.2026
    "ktru_catalog.jsonl.gz": (
        344_098_850,
        "eba3fc1009ce002f0bd38f516e0c50b53bb5f20b1b6af97edb3ce87a5081b8d1",
    ),
    "ktru_vectors_yandex.npz": (
        90_312_482,
        "4cdfb43563edda2d2b323564c96beedf59bec8f797779c54760cc922d3f1707d",
    ),
}
CHUNK = 1024 * 1024


def fetch(folder: Path, name: str, size: int, sha256: str) -> None:
    target = folder / name
    if target.is_file() and target.stat().st_size == size:
        print(f"{name}: есть", flush=True)
        return
    part = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    print(f"{name}: скачивается {size // CHUNK} МБ из {RELEASE}", flush=True)
    timeout = httpx.Timeout(60.0, connect=15.0)
    with httpx.stream("GET", f"{RELEASE}/{name}", follow_redirects=True, timeout=timeout) as answer:
        answer.raise_for_status()
        with part.open("wb") as out:
            for block in answer.iter_bytes(CHUNK):
                digest.update(block)
                out.write(block)
    if digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise ValueError(f"{name}: контрольная сумма не совпала, файл удалён")
    part.replace(target)
    print(f"{name}: готово, SHA-256 совпала", flush=True)


def main() -> None:
    folder = Path(sys.argv[1] if len(sys.argv) > 1 else "/catalog")
    for name, (size, sha256) in FILES.items():
        try:
            fetch(folder, name, size, sha256)
        except Exception as exc:  # noqa: BLE001 - the site shows the missing catalog itself
            print(
                f"{name}: не удалось получить ({type(exc).__name__}: {exc}). Скачайте файл со "
                f"страницы релиза catalog_yandex и положите в db/ (README, «Запуск»)",
                flush=True,
            )


if __name__ == "__main__":
    main()
