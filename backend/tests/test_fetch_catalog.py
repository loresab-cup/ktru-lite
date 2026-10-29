"""db/ files of a fresh clone: downloaded once, checked by SHA-256, never half written."""

import hashlib

import httpx
import pytest

import fetch_catalog


@pytest.fixture
def release(monkeypatch):
    body = b"catalog bytes"
    files = {"a.bin": (len(body), hashlib.sha256(body).hexdigest())}
    monkeypatch.setattr(fetch_catalog, "FILES", files)
    sent = []

    def stream(method, url, **kwargs):
        sent.append(url)
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=body))
        return httpx.Client(transport=transport).stream(method, url)

    monkeypatch.setattr(fetch_catalog.httpx, "stream", stream)
    return files, sent, body


def test_a_missing_file_is_downloaded_and_checked(release, tmp_path):
    files, sent, body = release
    fetch_catalog.fetch(tmp_path, "a.bin", *files["a.bin"])
    assert (tmp_path / "a.bin").read_bytes() == body and len(sent) == 1
    fetch_catalog.fetch(tmp_path, "a.bin", *files["a.bin"])  # there already: no second download
    assert len(sent) == 1


def test_a_wrong_checksum_leaves_no_file(release, tmp_path):
    files, _, _ = release
    size, _ = files["a.bin"]
    with pytest.raises(ValueError, match="контрольная сумма"):
        fetch_catalog.fetch(tmp_path, "a.bin", size, "0" * 64)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_download_does_not_stop_the_stack(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(fetch_catalog, "FILES", {"b.bin": (5, "0" * 64)})

    def offline(*args, **kwargs):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(fetch_catalog.httpx, "stream", offline)
    monkeypatch.setattr("sys.argv", ["fetch_catalog.py", str(tmp_path)])
    fetch_catalog.main()  # no exception: exit code 0, the site shows the missing catalog
    assert "не удалось получить" in capsys.readouterr().out
