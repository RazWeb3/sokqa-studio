import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.schemas.request import TtsRecordingTarget
from app.services.pack_listing import _list_packs_from_storage, _list_v2_items_for_content
from app.services.pack_paths import pack_root_prefix
from app.services.tts_recording_api import run_recording
from main import app


client = TestClient(app)


def _configure_storage(tmp_path: Path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")


def _doc_content(content_id: str, title: str = "ドキュメント1") -> dict:
    return {
        "id": f"{content_id}-document",
        "type": "document",
        "schemaVersion": 1,
        "title": title,
        "documents": [{"id": "d-1", "text": "本文です。"}],
    }


def _quiz_content(content_id: str, title: str = "クイズ1") -> dict:
    return {
        "id": f"{content_id}-quiz",
        "type": "quiz",
        "schemaVersion": 1,
        "title": title,
        "questions": [
            {
                "id": "q-1",
                "question": "問いですか?",
                "choices": ["A", "B", "C", "D"],
                "answerIndex": 0,
                "explanation": "解説です。",
            }
        ],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_v2_pack(
    tmp_path: Path,
    monkeypatch,
    creator_id: str,
    content_id: str,
    version_id: str,
    revision: int,
    *,
    manifest_title: str = "V2 Manifest",
) -> None:
    _configure_storage(tmp_path, monkeypatch)
    root_prefix = pack_root_prefix(creator_id, content_id)
    root = tmp_path / "generated" / root_prefix
    doc_fv = f"fv_{revision}_document_doc_01"
    quiz_fv = f"fv_{revision}_quiz_quiz_01"
    doc = _doc_content(content_id)
    quiz = _quiz_content(content_id)
    doc["assetBaseUrl"] = f"http://localhost:8000/generated/{root_prefix}"
    quiz["assetBaseUrl"] = f"http://localhost:8000/generated/{root_prefix}"
    _write_json(root / "objects" / "doc" / f"{doc_fv}.json", doc)
    _write_json(root / "objects" / "quiz" / f"{quiz_fv}.json", quiz)
    manifest = {
        "id": f"{content_id}_manifest_r{revision}",
        "type": "pack_manifest",
        "schemaVersion": 2,
        "contentId": content_id,
        "title": manifest_title,
        "creator": {"id": creator_id, "displayName": None},
        "revision": revision,
        "versionId": version_id,
        "buildId": f"build_{version_id.removeprefix('v')}",
        "generatedAt": "2026-06-13T12:00:00+09:00",
        "change": {"operation": "initial_generate"},
        "items": [
            {
                "kind": "document",
                "name": "doc_01.json",
                "title": doc["title"],
                "logicalId": "doc_01",
                "fileVersionId": doc_fv,
                "url": f"http://localhost:8000/generated/{root_prefix}/objects/doc/{doc_fv}.json",
            },
            {
                "kind": "quiz",
                "name": "quiz_01.json",
                "title": quiz["title"],
                "logicalId": "quiz_01",
                "fileVersionId": quiz_fv,
                "url": f"http://localhost:8000/generated/{root_prefix}/objects/quiz/{quiz_fv}.json",
            },
        ],
    }
    _write_json(root / "versions" / version_id / "manifest.json", manifest)
    latest = {
        "type": "pack_latest",
        "schemaVersion": 2,
        "creatorId": creator_id,
        "contentId": content_id,
        "storagePrefix": root_prefix,
        "versionId": version_id,
        "revision": revision,
        "manifestUrl": f"http://localhost:8000/generated/{root_prefix}/versions/{version_id}/manifest.json",
        "assetBaseUrl": f"http://localhost:8000/generated/{root_prefix}",
        "title": manifest_title,
        "description": "",
        "language": "ja",
        "generatedAt": "2026-06-13T12:00:00+09:00",
        "change": {"operation": "initial_generate"},
        "items": manifest["items"],
    }
    _write_json(root / "latest.json", latest)


def test_list_packs_filters_by_creator_id(tmp_path, monkeypatch) -> None:
    _write_v2_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260613_120000", 1)
    _write_v2_pack(tmp_path, monkeypatch, "creator_b", "content_b", "v20260613_120000", 1)

    response = client.get("/packs?creatorId=creator_a")

    assert response.status_code == 200
    data = response.json()
    assert [item["packName"] for item in data["items"]] == ["doc_01.json", "quiz_01.json"]
    assert {item["creatorId"] for item in data["items"]} == {"creator_a"}
    assert {item["schemaVersion"] for item in data["items"]} == {2}
    assert data["items"][0]["target"] == {
        "creatorId": "creator_a",
        "contentId": "content_a",
        "versionId": "v20260613_120000",
        "packName": "doc_01.json",
        "kind": "document",
    }


def test_list_packs_returns_only_latest_revision_per_content_id(tmp_path, monkeypatch) -> None:
    _write_v2_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260613_120000", 1, manifest_title="古い")
    _write_v2_pack(tmp_path, monkeypatch, "creator_a", "content_a", "v20260613_121000", 2, manifest_title="最新")
    _write_v2_pack(tmp_path, monkeypatch, "creator_a", "content_b", "v20260613_115000", 1)

    response = client.get("/packs?creatorId=creator_a")

    assert response.status_code == 200
    data = response.json()
    content_a_items = [item for item in data["items"] if item["contentId"] == "content_a"]
    assert [item["packName"] for item in content_a_items] == ["doc_01.json", "quiz_01.json"]
    assert {item["versionId"] for item in content_a_items} == {"v20260613_121000"}
    assert {item["revision"] for item in content_a_items} == {2}
    assert {item["contentId"] for item in data["items"]} == {"content_a", "content_b"}


def test_list_packs_skips_broken_latest_manifest_and_uses_valid_revision(tmp_path, monkeypatch) -> None:
    _write_v2_pack(tmp_path, monkeypatch, "creator_a", "content_v2", "v20260613_120000", 1, manifest_title="有効")
    latest_path = tmp_path / "generated" / pack_root_prefix("creator_a", "content_v2") / "latest.json"
    latest_path.write_text("{broken latest", encoding="utf-8")
    broken_dir = tmp_path / "generated" / pack_root_prefix("creator_a", "content_v2") / "versions" / "v20260613_121000"
    broken_dir.mkdir(parents=True, exist_ok=True)
    (broken_dir / "manifest.json").write_text("{broken json", encoding="utf-8")

    response = client.get("/packs?creatorId=creator_a")

    assert response.status_code == 200
    data = response.json()
    assert {item["versionId"] for item in data["items"]} == {"v20260613_120000"}
    assert {item["revision"] for item in data["items"]} == {1}


def test_list_packs_uses_latest_without_listing_versions(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
    monkeypatch.setattr(settings, "storage_backend", "gcs")

    class FakeStorage:
        def __init__(self) -> None:
            self.list_manifest_called = False

        def list_pack_prefixes_for_creator(self, creator_id: str | None = None) -> list[str]:
            assert creator_id == "creator_a"
            return ["sokqa/creators/creator_a/packs/content_latest"]

        def read_latest(self, prefix: str) -> dict:
            assert prefix == "sokqa/creators/creator_a/packs/content_latest"
            return {
                "type": "pack_latest",
                "schemaVersion": 2,
                "creatorId": "creator_a",
                "contentId": "content_latest",
                "storagePrefix": prefix,
                "versionId": "v20260613_123000",
                "revision": 4,
                "manifestUrl": f"http://localhost:8000/generated/{prefix}/versions/v20260613_123000/manifest.json",
                "assetBaseUrl": f"http://localhost:8000/generated/{prefix}",
                "title": "latestだけ読む",
                "description": "",
                "language": "ja",
                "generatedAt": "2026-06-13T12:30:00+09:00",
                "change": {"operation": "recording"},
                "items": [
                    {
                        "kind": "document",
                        "name": "doc_01.json",
                        "title": "本文タイトル",
                        "logicalId": "doc_01",
                        "fileVersionId": "fv_latest_doc_01",
                        "url": f"http://localhost:8000/generated/{prefix}/objects/doc/fv_latest_doc_01.json",
                    }
                ],
            }

        def list_manifests(self, prefix: str) -> list[str]:
            self.list_manifest_called = True
            raise AssertionError("versions list must not be called when latest exists")

    fake = FakeStorage()
    monkeypatch.setattr("app.services.pack_listing.StorageClient", lambda: fake)

    response = client.get("/packs?creatorId=creator_a")

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["versionId"] == "v20260613_123000"
    assert data["items"][0]["title"] == "本文タイトル"
    assert not fake.list_manifest_called


def test_list_packs_backfills_empty_latest_from_latest_manifest(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
    monkeypatch.setattr(settings, "storage_backend", "gcs")

    prefix = "sokqa/creators/creator_a/packs/content_empty_latest"

    class FakeStorage:
        def __init__(self) -> None:
            self.saved_latest: dict | None = None

        def list_pack_prefixes_for_creator(self, creator_id: str | None = None) -> list[str]:
            return [prefix]

        def read_latest(self, read_prefix: str) -> dict:
            assert read_prefix == prefix
            return {
                "type": "pack_latest",
                "schemaVersion": 2,
                "creatorId": "creator_a",
                "contentId": "content_empty_latest",
                "storagePrefix": prefix,
                "versionId": "v20260613_123000",
                "revision": 4,
                "manifestUrl": f"http://localhost:8000/generated/{prefix}/versions/v20260613_123000/manifest.json",
                "assetBaseUrl": f"http://localhost:8000/generated/{prefix}",
                "title": "空latest",
                "description": "",
                "language": "ja",
                "generatedAt": "2026-06-13T12:30:00+09:00",
                "change": {"operation": "recording"},
                "items": [],
            }

        def list_manifests(self, read_prefix: str) -> list[str]:
            assert read_prefix == prefix
            return ["versions/v20260613_123000/manifest.json"]

        def read_manifest(self, read_prefix: str, version_id: str) -> dict:
            assert read_prefix == prefix
            assert version_id == "v20260613_123000"
            return {
                "id": "content_empty_latest_manifest_r4",
                "type": "pack_manifest",
                "schemaVersion": 2,
                "contentId": "content_empty_latest",
                "title": "manifestから復旧",
                "creator": {"id": "creator_a", "displayName": None},
                "revision": 4,
                "versionId": version_id,
                "buildId": "build_20260613_123000",
                "generatedAt": "2026-06-13T12:30:00+09:00",
                "change": {"operation": "recording"},
                "items": [
                    {
                        "kind": "document",
                        "name": "doc_01.json",
                        "title": "本文タイトル",
                        "logicalId": "doc_01",
                        "fileVersionId": "fv_latest_doc_01",
                        "url": f"http://localhost:8000/generated/{prefix}/objects/doc/fv_latest_doc_01.json",
                    }
                ],
            }

        def save_latest(self, write_prefix: str, latest_json: dict) -> str:
            assert write_prefix == prefix
            self.saved_latest = latest_json
            return f"http://localhost:8000/generated/{prefix}/latest.json"

    fake = FakeStorage()
    monkeypatch.setattr("app.services.pack_listing.StorageClient", lambda: fake)

    items = _list_packs_from_storage("creator_a")

    assert len(items) == 1
    assert items[0]["title"] == "本文タイトル"
    assert fake.saved_latest is not None
    assert fake.saved_latest["title"] == "manifestから復旧"
    assert len(fake.saved_latest["items"]) == 1


def test_v2_listing_reads_only_latest_manifest_candidate(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")

    class FakeStorage:
        def __init__(self) -> None:
            self.reads: list[str] = []

        def list_manifests(self, prefix: str) -> list[str]:
            assert prefix == "sokqa/creators/creator_a/packs/content_v2"
            return [
                "versions/v20260613_120000/manifest.json",
                "versions/v20260613_121000/manifest.json",
                "versions/v20260613_122000/manifest.json",
            ]

        def read_manifest(self, prefix: str, version_id: str) -> dict:
            self.reads.append(version_id)
            return {
                "id": "content_v2_manifest_r3",
                "type": "pack_manifest",
                "schemaVersion": 2,
                "contentId": "content_v2",
                "title": "最新だけ読む",
                "creator": {"id": "creator_a", "displayName": None},
                "revision": 3,
                "versionId": version_id,
                "buildId": "build_20260613_122000",
                "generatedAt": "2026-06-13T12:20:00+09:00",
                "change": {"operation": "recording"},
                "items": [
                    {
                        "kind": "document",
                        "name": "doc_01.json",
                        "title": "本文タイトル",
                        "logicalId": "doc_01",
                        "fileVersionId": "fv_latest_doc_01",
                        "url": "http://localhost:8000/generated/sokqa/creators/creator_a/packs/content_v2/objects/doc/fv_latest_doc_01.json",
                    }
                ],
            }

    storage = FakeStorage()

    items = _list_v2_items_for_content(storage, "creator_a", "content_v2")

    assert storage.reads == ["v20260613_122000"]
    assert len(items) == 1
    assert items[0]["title"] == "本文タイトル"


def test_list_packs_includes_v2_manifest_created_by_recording(tmp_path, monkeypatch) -> None:
    _write_v2_pack(tmp_path, monkeypatch, "creator_test", "content_test", "v20260613_120000", 1)

    result = run_recording(
        target=TtsRecordingTarget(
            creatorId="creator_test",
            contentId="content_test",
            versionId="v20260613_120000",
            packName="quiz_01.json",
        ),
        unit_ids=["q_q-1_choice_0"],
        synthesize_fn=lambda text: b"mp3",
    )
    response = client.get("/packs?creatorId=creator_test")

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    quiz_item = next(item for item in data["items"] if item["kind"] == "quiz")
    assert quiz_item["schemaVersion"] == 2
    assert quiz_item["revision"] == 2
    assert quiz_item["versionId"] == result["versionId"]
    assert quiz_item["title"] == "クイズ1"
    assert quiz_item["manifestUrl"].endswith(f"/versions/{result['versionId']}/manifest.json")
    assert quiz_item["url"].startswith("http://localhost:8000/generated/sokqa/creators/creator_test/packs/content_test/objects/quiz/")
