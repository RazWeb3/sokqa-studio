import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.pack_paths import pack_root_prefix
from main import app


client = TestClient(app)


def _configure_local_storage(tmp_path: Path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")


def _write_pack_root(tmp_path: Path, monkeypatch, creator_id: str, content_id: str, version_id: str) -> str:
    _configure_local_storage(tmp_path, monkeypatch)
    storage_prefix = pack_root_prefix(creator_id, content_id)
    target_dir = tmp_path / "generated" / storage_prefix
    (target_dir / "objects" / "audio").mkdir(parents=True, exist_ok=True)
    (target_dir / "versions" / version_id).mkdir(parents=True, exist_ok=True)
    (target_dir / "versions" / version_id / "manifest.json").write_text(
        json.dumps(
            {
                "id": "manifest",
                "type": "pack_manifest",
                "schemaVersion": 2,
                "contentId": content_id,
                "creator": {"id": creator_id},
                "revision": 1,
                "versionId": version_id,
                "buildId": "build_20260609_120000",
                "generatedAt": "2026-06-09T12:00:00+09:00",
                "change": {"operation": "initial_generate"},
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    (target_dir / "objects" / "doc" / "fv_doc_01.json").parent.mkdir(parents=True, exist_ok=True)
    (target_dir / "objects" / "doc" / "fv_doc_01.json").write_text(
        json.dumps(
            {
                "id": "doc",
                "type": "document",
                "schemaVersion": 1,
                "title": "doc",
                "documents": [{"id": "doc-1", "text": "本文です。"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (target_dir / "objects" / "audio" / "av_doc_01__doc-1.mp3").write_bytes(b"mp3")
    return storage_prefix


def test_delete_pack_removes_local_manifest_json_and_audio(tmp_path, monkeypatch) -> None:
    storage_prefix = _write_pack_root(tmp_path, monkeypatch, "creator_demo", "cnt_delete", "v20260609_120000")
    target_dir = tmp_path / "generated" / storage_prefix

    response = client.post("/packs/delete", json={"storagePrefix": storage_prefix})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "deleted"
    assert data["storagePrefix"] == storage_prefix
    assert data["objectCount"] == 3
    assert data["deletedCount"] == 3
    assert sorted(data["objectNames"]) == [
        f"{storage_prefix}/objects/audio/av_doc_01__doc-1.mp3",
        f"{storage_prefix}/objects/doc/fv_doc_01.json",
        f"{storage_prefix}/versions/v20260609_120000/manifest.json",
    ]
    assert not target_dir.exists()


def test_delete_pack_returns_404_when_prefix_has_no_objects(tmp_path, monkeypatch) -> None:
    _configure_local_storage(tmp_path, monkeypatch)
    storage_prefix = pack_root_prefix("creator_demo", "cnt_missing")

    response = client.post("/packs/delete", json={"storagePrefix": storage_prefix})

    assert response.status_code == 404
    assert "no objects found" in response.json()["detail"]


def test_delete_pack_rejects_unsafe_prefix(tmp_path, monkeypatch) -> None:
    _configure_local_storage(tmp_path, monkeypatch)

    response = client.post("/packs/delete", json={"storagePrefix": "sokqa"})

    assert response.status_code == 400
    assert "single pack root" in response.json()["detail"]
