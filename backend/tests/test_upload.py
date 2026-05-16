import io

import pytest
from werkzeug.datastructures import MultiDict

from app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect UPLOAD_ROOT to a temp dir so tests don't pollute the repo.
    from app.api import upload as upload_mod

    monkeypatch.setattr(upload_mod, "UPLOAD_ROOT", tmp_path / "uploads")
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _md(content: str = "# hello\n"):
    return (io.BytesIO(content.encode("utf-8")), "x.md")


def test_upload_single_file(client):
    data = {"files": _md(), "paths": "a.md"}
    r = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200, r.data
    body = r.get_json()
    assert body["file_count"] == 1
    assert body["files"] == ["a.md"]


def test_upload_preserves_subfolders(client):
    data = MultiDict([
        ("files", (io.BytesIO(b"a"), "x.md")),
        ("paths", "folder/sub/a.md"),
        ("files", (io.BytesIO(b"b"), "y.md")),
        ("paths", "folder/b.md"),
    ])
    r = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200
    body = r.get_json()
    assert body["file_count"] == 2
    assert set(body["files"]) == {"folder/sub/a.md", "folder/b.md"}


def test_upload_rejects_path_traversal(client):
    data = MultiDict([("files", (io.BytesIO(b"x"), "x.md")), ("paths", "../escape.md")])
    r = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 422


def test_upload_rejects_when_no_md(client):
    data = MultiDict([("files", (io.BytesIO(b"x"), "x.txt")), ("paths", "x.txt")])
    r = client.post("/api/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 422
