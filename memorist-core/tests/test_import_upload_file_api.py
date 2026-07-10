import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from memcore.config import get_settings
from memcore.main import create_app
from memcore.storage.sqlite import connect


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("MEMORIST_DB_PATH", str(tmp_path / "memorist.sqlite"))
    monkeypatch.setenv("MEMORIST_OBJECT_STORE_PATH", str(tmp_path / "objects"))
    return TestClient(create_app())


def _zip_bytes(name: str = "conversations.json") -> bytes:
    payload = json.dumps([{"id": "c1", "title": "t", "mapping": {}}]).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(name, payload)
    return out.getvalue()


def test_multipart_zip_upload_succeeds(client: TestClient) -> None:
    response = client.post(
        "/memcore/imports/upload-file",
        files={"file": ("chatgpt.zip", _zip_bytes(), "application/zip")},
        data={"mode": "inspect", "processing_mode": "none"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "staged"
    assert body["total_files"] == 1


def test_multipart_json_upload_succeeds_and_sanitizes_name(client: TestClient) -> None:
    response = client.post(
        "/memcore/imports/upload-file",
        files={"file": ("../../evil.json", b'{"items": []}', "application/json")},
    )
    assert response.status_code == 200
    run_uuid = response.json()["import_run_uuid"]
    settings = get_settings()
    connection = connect(settings.db_path)
    try:
        artifact = connection.execute(
            (
                "SELECT relative_path, object_store_path "
                "FROM import_artifacts WHERE import_run_uuid = ?"
            ),
            (run_uuid,),
        ).fetchone()
    finally:
        connection.close()
    assert artifact["relative_path"] == "evil.json"
    assert Path(artifact["object_store_path"]).is_relative_to(Path(settings.object_store_path))


def test_upload_rejects_unsupported_extension_and_does_not_create_run(client: TestClient) -> None:
    response = client.post(
        "/memcore/imports/upload-file",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert "Unsupported" in response.json()["detail"]
    assert client.get("/memcore/imports").json()["items"] == []


def test_zip_traversal_rejected_and_partial_cleaned(client: TestClient) -> None:
    response = client.post(
        "/memcore/imports/upload-file",
        files={"file": ("bad.zip", _zip_bytes("../evil.json"), "application/zip")},
    )
    assert response.status_code == 400
    assert "path traversal" in response.json()["detail"]
    settings = get_settings()
    upload_dir = Path(settings.object_store_path) / "imports" / "_uploads"
    assert not list(upload_dir.glob("upload-*"))
    assert client.get("/memcore/imports").json()["items"] == []


def test_absolute_zip_path_rejected(client: TestClient) -> None:
    response = client.post(
        "/memcore/imports/upload-file",
        files={"file": ("bad.zip", _zip_bytes("/tmp/evil.json"), "application/zip")},
    )
    assert response.status_code == 400
    assert "absolute path" in response.json()["detail"]


def test_browser_endpoint_does_not_accept_archive_path_json(client: TestClient) -> None:
    response = client.post(
        "/memcore/imports/upload-file",
        json={"archive_path": "/etc/passwd"},
    )
    assert response.status_code == 422


def test_malformed_json_stages_but_inspect_reports_safe_error(client: TestClient) -> None:
    response = client.post(
        "/memcore/imports/upload-file",
        files={"file": ("conversations.json", b"{not json", "application/json")},
    )
    assert response.status_code == 200
    run_uuid = response.json()["import_run_uuid"]
    inspect = client.post(f"/memcore/imports/{run_uuid}/inspect")
    assert inspect.status_code == 200
    assert inspect.json()["error_count"] == 1


def test_recent_import_runs_endpoint_returns_safe_summaries(client: TestClient) -> None:
    client.post(
        "/memcore/imports/upload-file",
        files={"file": ("chatgpt.zip", _zip_bytes(), "application/zip")},
    )
    items = client.get("/memcore/imports?limit=5").json()["items"]
    assert len(items) == 1
    assert "object_store_path" not in items[0]
    assert "archive_sha256" not in items[0]
