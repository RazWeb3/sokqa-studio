import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.pack_metadata import pack_storage_prefix
from main import app


client = TestClient(app)


def _write_pack(tmp_path: Path, monkeypatch, creator_id: str, content_id: str, version_id: str, name: str, kind: str) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")

    prefix = pack_storage_prefix(creator_id, content_id, version_id)
    target_dir = tmp_path / "generated" / prefix
    target_dir.mkdir(parents=True, exist_ok=True)
    content = {
        "id": f"{content_id}-{kind}",
        "type": kind,
        "schemaVersion": 1,
        "title": f"{content_id} {kind}",
    }
    if kind == "quiz":
        content["questions"] = [
            {
                "id": "q-1",
                "question": "問いですか?",
                "choices": ["A", "B", "C", "D"],
                "answerIndex": 0,
                "explanation": "解説です。",
            }
        ]
    else:
        content["documents"] = [{"id": "d-1", "text": "本文です。"}]
    (target_dir / name).write_text(json.dumps(content, ensure_ascii=False), encoding="utf-8")
    (target_dir / "manifest.json").write_text(
        json.dumps(
            {"id": "manifest", "type": "pack_manifest", "title": f"{content_id} manifest", "items": []},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_list_packs_filters_by_creator_id(tmp_path, monkeypatch) -> None:
    _write_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260608_120000", "quiz.json", "quiz")
    _write_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260608_120000", "doc.json", "document")
    _write_pack(tmp_path, monkeypatch, "creator_b", "content_b", "v20260608_120000", "quiz.json", "quiz")

    response = client.get("/packs?creatorId=creator_a")

    assert response.status_code == 200
    data = response.json()
    assert [item["packName"] for item in data["items"]] == ["doc.json", "quiz.json"]
    assert {item["creatorId"] for item in data["items"]} == {"creator_a"}
    assert data["items"][0]["target"] == {
        "creatorId": "creator_a",
        "contentId": "content_a",
        "versionId": "v20260608_120000",
        "packName": "doc.json",
        "kind": "document",
    }
    assert data["items"][0]["url"].endswith("/sokqa/creators/creator_a/packs/content_a/versions/v20260608_120000/doc.json")
    assert data["items"][0]["manifestTitle"] == "content_a manifest"
    assert data["items"][0]["manifestUrl"].endswith(
        "/sokqa/creators/creator_a/packs/content_a/versions/v20260608_120000/manifest.json"
    )


def test_list_packs_returns_only_latest_version_per_content_id(tmp_path, monkeypatch) -> None:
    _write_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260608_120000", "quiz.json", "quiz")
    _write_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260608_120000", "doc.json", "document")
    _write_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260609_120000", "quiz.json", "quiz")
    _write_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260609_120000", "doc.json", "document")
    _write_pack(tmp_path, monkeypatch, "creator_a", "content_b", "v20260607_120000", "quiz.json", "quiz")

    response = client.get("/packs?creatorId=creator_a")

    assert response.status_code == 200
    data = response.json()
    content_a_items = [item for item in data["items"] if item["contentId"] == "content_a"]
    assert [item["packName"] for item in content_a_items] == ["doc.json", "quiz.json"]
    assert {item["versionId"] for item in content_a_items} == {"v20260609_120000"}
    assert {item["contentId"] for item in data["items"]} == {"content_a", "content_b"}


def test_list_packs_skips_incomplete_versions_without_manifest(tmp_path, monkeypatch) -> None:
    _write_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260608_120000", "quiz.json", "quiz")
    settings = get_settings()
    incomplete_prefix = pack_storage_prefix("creator_a", "content_a", "v20260609_120000")
    incomplete_dir = Path(settings.local_storage_dir) / incomplete_prefix
    incomplete_dir.mkdir(parents=True, exist_ok=True)
    (incomplete_dir / "quiz.json").write_text(
        json.dumps(
            {
                "id": "content_a-quiz",
                "type": "quiz",
                "schemaVersion": 1,
                "title": "Incomplete",
                "questions": [
                    {
                        "id": "q-1",
                        "question": "問いですか?",
                        "choices": ["A", "B", "C", "D"],
                        "answerIndex": 0,
                        "explanation": "解説です。",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = client.get("/packs?creatorId=creator_a")

    assert response.status_code == 200
    data = response.json()
    assert [item["versionId"] for item in data["items"]] == ["v20260608_120000"]
