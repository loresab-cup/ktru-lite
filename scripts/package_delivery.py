"""Build a source handoff with verified catalog files, excluding local secrets and caches."""

import ast
import hashlib
from pathlib import Path
import subprocess
import sys
import zipfile


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def allowed(path: str) -> bool:
    parts = Path(path).parts
    name = Path(path).name.lower()
    return (
        not any(p in {".git", ".venv", "node_modules", "outputs", "__pycache__"} for p in parts)
        and ((not name.startswith(".env") and not name.endswith(".env")) or name == ".env.example")
        and Path(name).suffix
        not in {".pem", ".key", ".p12", ".pfx", ".dump", ".db", ".sqlite", ".local"}
        and name not in {"id_rsa", "id_ed25519"}
    )


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    destination = (
        Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "outputs/delivery.zip"
    )
    tree = ast.parse((root / "backend/fetch_catalog.py").read_text(encoding="utf-8"))
    manifest = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "FILES" for t in n.targets)
    )
    catalog_paths = []
    for name, (size, expected) in manifest.items():
        path = root / "db" / name
        if not path.is_file() or path.stat().st_size != size:
            raise SystemExit(f"Missing or wrong size: db/{name}")
        digest = sha256(path)
        if digest != expected:
            raise SystemExit(f"Checksum mismatch: db/{name}")
        catalog_paths.append(path.relative_to(root).as_posix())
    listed = (
        subprocess.check_output(["git", "ls-files", "-z", "--cached"], cwd=root)
        .decode("utf-8")
        .split("\0")
    )
    paths = sorted(
        {p for p in [*listed, *catalog_paths] if p and allowed(p) and (root / p).is_file()}
    )
    for name in paths:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise SystemExit(f"Refusing a path outside the source tree: {name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    checksums = []
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in paths:
            file = root / name
            digest = sha256(file)
            checksums.append(f"{digest}  {name}")
            archive.write(
                file,
                "ktru-lite/" + name,
                compress_type=zipfile.ZIP_STORED if name in catalog_paths else zipfile.ZIP_DEFLATED,
            )
        archive.writestr("ktru-lite/DELIVERY-SHA256SUMS.txt", "\n".join(checksums) + "\n")
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert not any(name.endswith("/.env") for name in archive.namelist())
    print(f"Ready: {destination} ({destination.stat().st_size:,} bytes, {len(paths)} files)")


if __name__ == "__main__":
    main()
