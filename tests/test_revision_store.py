from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import app.services.storage_client as storage_module
from app.config import get_settings
from app.schemas.pack_v2 import (
    AddedPackFile,
    AudioObject,
    ChangedPackFile,
    CommitPackRevisionInput,
    RemovedPackFile,
    RevisionTarget,
)
from app.services.pack_paths import pack_root_prefix
from app.services.revision_store import persist_revision_commit, read_pack_manifest_v2
from app.services.storage_client import StorageClient


JST = timezone(timedelta(hours=9))
NOW_1 = datetime(2026, 6, 13, 11, 0, 0, tzinfo=JST)
NOW_2 = datetime(2026, 6, 13, 11, 1, 0, tzinfo=JST)
PUBLIC_BASE = "https://cdn.example.com"


def _configure_local(tmp_path: Path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", PUBLIC_BASE)
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")


def _configure_gcs(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "gcs")
    monkeypatch.setattr(settings, "gcs_bucket", "bucket-test")
    monkeypatch.setattr(settings, "public_base_url", PUBLIC_BASE)
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")


def _target(version_id: str | None = None) -> RevisionTarget:
    return RevisionTarget(creatorId="creator_default", contentId="cnt_store", versionId=version_id)


def _added_doc(logical_id: str = "doc_01") -> AddedPackFile:
    return AddedPackFile(
        name=f"{logical_id}.json",
        kind="document",
        logicalId=logical_id,
        content={
            "id": logical_id,
            "type": "document_pack",
            "schemaVersion": 1,
            "documents": [{"id": "doc-1", "text": "本文"}],
        },
    )


def _added_quiz(logical_id: str = "quiz_01") -> AddedPackFile:
    return AddedPackFile(
        name=f"{logical_id}.json",
        kind="quiz",
        logicalId=logical_id,
        content={
            "id": logical_id,
            "type": "quiz_pack",
            "schemaVersion": 1,
            "questions": [{"id": "q-1", "question": "問題", "choices": ["A", "B", "C", "D"], "answerIndex": 0}],
        },
    )


def _changed_doc(logical_id: str, previous: str) -> ChangedPackFile:
    return ChangedPackFile(
        name=f"{logical_id}.json",
        kind="document",
        logicalId=logical_id,
        previousFileVersionId=previous,
        content={
            "id": logical_id,
            "type": "document_pack",
            "schemaVersion": 1,
            "documents": [{"id": "doc-1", "text": "更新本文"}],
        },
    )


class RecordingStorage(StorageClient):
    def __init__(self, *, fail_on_object_call: int | None = None) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []
        self._object_call_count = 0
        self._fail_on_object_call = fail_on_object_call
        self._saving_manifest = False

    def save_object(self, prefix: str, relative_path: str, data: bytes | str, content_type: str) -> str:
        if self._saving_manifest:
            return super().save_object(prefix, relative_path, data, content_type)
        self._object_call_count += 1
        self.calls.append(("object", relative_path))
        if self._fail_on_object_call == self._object_call_count:
            raise RuntimeError("injected object save failure")
        return super().save_object(prefix, relative_path, data, content_type)

    def save_manifest(self, prefix: str, version_id: str, manifest_json: dict | str) -> str:
        self.calls.append(("manifest", version_id))
        self._saving_manifest = True
        try:
            return super().save_manifest(prefix, version_id, manifest_json)
        finally:
            self._saving_manifest = False


def test_initial_commit_persists_objects_then_manifest_and_can_read_back(tmp_path, monkeypatch) -> None:
    _configure_local(tmp_path, monkeypatch)
    storage = RecordingStorage()
    request = CommitPackRevisionInput(
        target=_target(),
        operation="initial_generate",
        addedFiles=[_added_doc("doc_01"), _added_quiz("quiz_01")],
    )

    result = persist_revision_commit(storage, None, request, now=NOW_1, public_base_url=PUBLIC_BASE)
    prefix = pack_root_prefix("creator_default", "cnt_store")

    assert storage.calls[-1] == ("manifest", result.versionId)
    assert all(kind == "object" for kind, _ in storage.calls[:-1])
    assert storage.list_manifests(prefix) == ["versions/v20260613_110000/manifest.json"]

    manifest = read_pack_manifest_v2(storage, "creator_default", "cnt_store", result.versionId)
    assert manifest.revision == 1
    assert {item.logicalId for item in manifest.items} == {"doc_01", "quiz_01"}
    doc_payload = json.loads(storage.read_object(prefix, result.docObjects[0].relativePath).decode("utf-8"))
    assert doc_payload["assetBaseUrl"] == result.assetBaseUrl


def test_changed_commit_saves_only_changed_file_object(tmp_path, monkeypatch) -> None:
    _configure_local(tmp_path, monkeypatch)
    storage = RecordingStorage()
    initial = persist_revision_commit(
        storage,
        None,
        CommitPackRevisionInput(
            target=_target(),
            operation="initial_generate",
            addedFiles=[_added_doc("doc_01"), _added_quiz("quiz_01")],
        ),
        now=NOW_1,
        public_base_url=PUBLIC_BASE,
    )
    doc_item = next(item for item in initial.items if item.logicalId == "doc_01")
    storage.calls.clear()

    result = persist_revision_commit(
        storage,
        initial.manifest,
        CommitPackRevisionInput(
            target=_target(initial.versionId),
            operation="text_fix",
            changedFiles=[_changed_doc("doc_01", doc_item.fileVersionId)],
        ),
        now=NOW_2,
        public_base_url=PUBLIC_BASE,
    )

    assert [call[0] for call in storage.calls] == ["object", "manifest"]
    assert storage.calls[0][1].startswith("objects/doc/")
    assert not result.quizObjects
    unchanged_quiz = next(item for item in result.items if item.logicalId == "quiz_01")
    previous_quiz = next(item for item in initial.items if item.logicalId == "quiz_01")
    assert unchanged_quiz.fileVersionId == previous_quiz.fileVersionId


def test_recording_audio_object_is_saved_without_copying_old_audio(tmp_path, monkeypatch) -> None:
    _configure_local(tmp_path, monkeypatch)
    storage = RecordingStorage()
    initial = persist_revision_commit(
        storage,
        None,
        CommitPackRevisionInput(target=_target(), operation="initial_generate", addedFiles=[_added_doc("doc_01")]),
        now=NOW_1,
        public_base_url=PUBLIC_BASE,
    )
    storage.calls.clear()

    result = persist_revision_commit(
        storage,
        initial.manifest,
        CommitPackRevisionInput(
            target=_target(initial.versionId),
            operation="recording",
            newAudioObjects=[
                AudioObject(
                    audioVersionId="av_20260613_110100_doc_01__doc-1_abcd1234",
                    relativePath="objects/audio/av_20260613_110100_doc_01__doc-1_abcd1234.mp3",
                    data=b"mp3-data",
                )
            ],
        ),
        now=NOW_2,
        public_base_url=PUBLIC_BASE,
    )
    prefix = pack_root_prefix("creator_default", "cnt_store")

    assert storage.calls == [
        ("object", "objects/audio/av_20260613_110100_doc_01__doc-1_abcd1234.mp3"),
        ("manifest", result.versionId),
    ]
    assert storage.read_object(prefix, "objects/audio/av_20260613_110100_doc_01__doc-1_abcd1234.mp3") == b"mp3-data"


def test_removed_file_updates_manifest_without_deleting_or_resaving_existing_object(tmp_path, monkeypatch) -> None:
    _configure_local(tmp_path, monkeypatch)
    storage = RecordingStorage()
    initial = persist_revision_commit(
        storage,
        None,
        CommitPackRevisionInput(
            target=_target(),
            operation="initial_generate",
            addedFiles=[_added_doc("doc_01"), _added_quiz("quiz_01")],
        ),
        now=NOW_1,
        public_base_url=PUBLIC_BASE,
    )
    doc_item = next(item for item in initial.items if item.logicalId == "doc_01")
    prefix = pack_root_prefix("creator_default", "cnt_store")
    old_doc_path = next(obj.relativePath for obj in initial.docObjects if obj.logicalId == "doc_01")
    storage.calls.clear()

    result = persist_revision_commit(
        storage,
        initial.manifest,
        CommitPackRevisionInput(
            target=_target(initial.versionId),
            operation="remove_file",
            removedFiles=[RemovedPackFile(logicalId="doc_01", previousFileVersionId=doc_item.fileVersionId)],
        ),
        now=NOW_2,
        public_base_url=PUBLIC_BASE,
    )

    assert storage.calls == [("manifest", result.versionId)]
    assert {item.logicalId for item in result.items} == {"quiz_01"}
    assert storage.read_object(prefix, old_doc_path)


def test_object_save_failure_does_not_write_manifest(tmp_path, monkeypatch) -> None:
    _configure_local(tmp_path, monkeypatch)
    storage = RecordingStorage(fail_on_object_call=2)
    request = CommitPackRevisionInput(
        target=_target(),
        operation="initial_generate",
        addedFiles=[_added_doc("doc_01"), _added_quiz("quiz_01")],
    )
    prefix = pack_root_prefix("creator_default", "cnt_store")

    with pytest.raises(RuntimeError, match="injected object save failure"):
        persist_revision_commit(storage, None, request, now=NOW_1, public_base_url=PUBLIC_BASE)

    assert ("manifest", "v20260613_110000") not in storage.calls
    with pytest.raises(FileNotFoundError):
        storage.read_manifest(prefix, "v20260613_110000")


def test_unsafe_audio_path_is_rejected_before_storage_write(tmp_path, monkeypatch) -> None:
    _configure_local(tmp_path, monkeypatch)
    storage = RecordingStorage()
    request = CommitPackRevisionInput(
        target=_target(),
        operation="recording",
        newAudioObjects=[
            AudioObject(
                audioVersionId="av_safe",
                relativePath="objects/%2e%2e/audio.mp3",
                data=b"mp3",
            )
        ],
    )

    with pytest.raises(Exception):
        persist_revision_commit(storage, None, request, now=NOW_1, public_base_url=PUBLIC_BASE)

    assert storage.calls == []


class FakeBlob:
    def __init__(self, name: str, bucket: "FakeBucket") -> None:
        self.name = name
        self.bucket = bucket

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self.bucket.calls.append(("upload", self.name, content_type))
        self.bucket.objects[self.name] = data

    def download_as_bytes(self) -> bytes:
        return self.bucket.objects[self.name]


class FakeBucket:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.objects: dict[str, bytes] = {}
        self.copy_called = False

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(name, self)

    def list_blobs(self, prefix: str):
        for name in sorted(self.objects):
            if name.startswith(prefix):
                yield type("BlobRecord", (), {"name": name})()

    def copy_blob(self, *args, **kwargs):  # pragma: no cover - called only on regression
        self.copy_called = True
        raise AssertionError("copy_blob must not be used by revision_store")


class FakeStorageClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self.bucket = bucket

    def bucket(self, name: str) -> FakeBucket:
        return self.bucket


def test_gcs_v2_save_uses_uploads_in_order_and_never_copy_prefix(monkeypatch) -> None:
    _configure_gcs(monkeypatch)
    fake_bucket = FakeBucket()

    class ClientFactory:
        def bucket(self, name: str) -> FakeBucket:
            assert name == "bucket-test"
            return fake_bucket

    monkeypatch.setattr(storage_module.storage, "Client", lambda: ClientFactory())
    storage = StorageClient()
    request = CommitPackRevisionInput(
        target=_target(),
        operation="initial_generate",
        addedFiles=[_added_doc("doc_01"), _added_quiz("quiz_01")],
        newAudioObjects=[
            AudioObject(
                audioVersionId="av_20260613_110000_audio_abcd1234",
                relativePath="objects/audio/av_20260613_110000_audio_abcd1234.mp3",
                data=b"mp3",
            )
        ],
    )

    result = persist_revision_commit(storage, None, request, now=NOW_1, public_base_url=PUBLIC_BASE)

    uploaded_names = [call[1] for call in fake_bucket.calls]
    assert uploaded_names[-1].endswith(f"versions/{result.versionId}/manifest.json")
    assert any("/objects/doc/" in name for name in uploaded_names)
    assert any("/objects/quiz/" in name for name in uploaded_names)
    assert any(name.endswith("/objects/audio/av_20260613_110000_audio_abcd1234.mp3") for name in uploaded_names)
    assert not fake_bucket.copy_called
    assert storage.list_manifests(pack_root_prefix("creator_default", "cnt_store")) == [
        "versions/v20260613_110000/manifest.json"
    ]
