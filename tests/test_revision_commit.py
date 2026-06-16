from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.pack_v2 import (
    AddedPackFile,
    AudioObject,
    ChangedPackFile,
    ChangedUnit,
    CommitPackRevisionInput,
    ManifestChange,
    ManifestCreatorV2,
    ManifestItemV2,
    PackManifestV2,
    ReRecordNeededUnit,
    RemovedPackFile,
    RevisionTarget,
)
from app.services.revision_commit import RevisionCommitError, build_revision_commit


JST = timezone(timedelta(hours=9))
NOW_1 = datetime(2026, 6, 13, 10, 0, 0, tzinfo=JST)
NOW_2 = datetime(2026, 6, 13, 10, 1, 0, tzinfo=JST)
NOW_3 = datetime(2026, 6, 13, 10, 2, 0, tzinfo=JST)
PUBLIC_BASE = "https://cdn.example.com"


def _target(version_id: str | None = None) -> RevisionTarget:
    return RevisionTarget(creatorId="creator_default", contentId="cnt_test", versionId=version_id)


def _doc_file(logical_id: str = "doc_01", *, previous: str = "fv_previous") -> ChangedPackFile:
    return ChangedPackFile(
        name=f"{logical_id}.json",
        kind="document",
        logicalId=logical_id,
        previousFileVersionId=previous,
        content={
            "id": logical_id,
            "type": "document_pack",
            "schemaVersion": 1,
            "title": f"{logical_id} title",
            "items": [{"id": "doc-1", "text": "本文"}],
        },
    )


def _added_doc(logical_id: str = "doc_01") -> AddedPackFile:
    return AddedPackFile(
        name=f"{logical_id}.json",
        kind="document",
        logicalId=logical_id,
        content={
            "id": logical_id,
            "type": "document_pack",
            "schemaVersion": 1,
            "title": f"{logical_id} title",
            "items": [{"id": "doc-1", "text": "本文"}],
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
            "title": f"{logical_id} title",
            "questions": [{"id": "q-1", "question": "問題", "choices": ["A", "B", "C", "D"], "answerIndex": 0}],
        },
    )


def _initial_manifest() -> PackManifestV2:
    request = CommitPackRevisionInput(
        target=_target(),
        operation="initial_generate",
        addedFiles=[_added_doc("doc_01"), _added_quiz("quiz_01")],
    )
    return build_revision_commit(None, request, now=NOW_1, public_base_url=PUBLIC_BASE).manifest


def test_manifest_v2_validates_required_fields_and_unique_logical_ids() -> None:
    item = ManifestItemV2(
        kind="document",
        name="doc_01.json",
        logicalId="doc_01",
        fileVersionId="fv_doc_01",
        url="https://cdn.example.com/objects/doc/fv_doc_01.json",
    )

    manifest = PackManifestV2(
        id="cnt_test_manifest_r1",
        contentId="cnt_test",
        creator=ManifestCreatorV2(id="creator_default"),
        revision=1,
        versionId="v20260613_100000",
        buildId="build_20260613_100000",
        generatedAt="2026-06-13T10:00:00+09:00",
        change=ManifestChange(operation="initial_generate"),
        items=[item],
    )

    assert manifest.schemaVersion == 1
    with pytest.raises(ValidationError):
        PackManifestV2.model_validate({})
    with pytest.raises(ValidationError):
        PackManifestV2.model_validate({**manifest.model_dump(), "items": [item.model_dump(), item.model_dump()]})


def test_initial_commit_creates_revision_one_and_objects_to_save() -> None:
    request = CommitPackRevisionInput(
        target=_target(),
        operation="initial_generate",
        addedFiles=[_added_doc("doc_01"), _added_quiz("quiz_01")],
        changedUnits=[ChangedUnit(fileName="doc_01.json", unitId="doc-1", fields=["text"], category="initial")],
    )

    result = build_revision_commit(None, request, now=NOW_1, public_base_url=PUBLIC_BASE)

    assert result.revision == 1
    assert result.sourceVersionId is None
    assert result.versionId == "v20260613_100000"
    assert result.assetBaseUrl == "https://cdn.example.com/sokqa/creators/creator_default/packs/cnt_test"
    assert result.manifestUrl.endswith("/versions/v20260613_100000/manifest.json")
    assert result.addedFiles == ["doc_01.json", "quiz_01.json"]
    assert len(result.docObjects) == 1
    assert len(result.quizObjects) == 1
    assert result.docObjects[0].relativePath.startswith("objects/doc/")
    assert result.quizObjects[0].relativePath.startswith("objects/quiz/")
    assert result.docObjects[0].content["assetBaseUrl"] == result.assetBaseUrl
    assert result.manifest.change.changedUnits[0].unitId == "doc-1"
    assert [item.title for item in result.items] == ["doc_01 title", "quiz_01 title"]


def test_changed_file_replaces_only_target_item_and_carries_others_forward() -> None:
    current = _initial_manifest()
    doc_item = next(item for item in current.items if item.logicalId == "doc_01")
    quiz_item = next(item for item in current.items if item.logicalId == "quiz_01")
    request = CommitPackRevisionInput(
        target=_target(current.versionId),
        operation="text_fix",
        changedFiles=[_doc_file("doc_01", previous=doc_item.fileVersionId)],
        reRecordNeededUnits=[ReRecordNeededUnit(fileName="doc_01.json", unitId="doc-1", reason="text_changed")],
    )

    result = build_revision_commit(current, request, now=NOW_2, public_base_url=PUBLIC_BASE)
    next_doc = next(item for item in result.items if item.logicalId == "doc_01")
    next_quiz = next(item for item in result.items if item.logicalId == "quiz_01")

    assert result.revision == 2
    assert result.sourceVersionId == current.versionId
    assert next_doc.fileVersionId != doc_item.fileVersionId
    assert next_quiz.fileVersionId == quiz_item.fileVersionId
    assert next_doc.title == "doc_01 title"
    assert next_quiz.title == quiz_item.title
    assert result.changedFiles == ["doc_01.json"]
    assert result.reRecordNeededUnits[0].reason == "text_changed"
    assert len(result.docObjects) == 1
    assert not result.quizObjects


def test_changed_file_rejects_previous_file_version_mismatch() -> None:
    current = _initial_manifest()
    request = CommitPackRevisionInput(
        target=_target(current.versionId),
        operation="text_fix",
        changedFiles=[_doc_file("doc_01", previous="fv_wrong")],
    )

    with pytest.raises(RevisionCommitError, match="previousFileVersionId mismatch"):
        build_revision_commit(current, request, now=NOW_2, public_base_url=PUBLIC_BASE)


def test_added_existing_logical_id_is_rejected() -> None:
    current = _initial_manifest()
    request = CommitPackRevisionInput(
        target=_target(current.versionId),
        operation="add_file",
        addedFiles=[_added_doc("doc_01")],
    )

    with pytest.raises(RevisionCommitError, match="already exists"):
        build_revision_commit(current, request, now=NOW_2, public_base_url=PUBLIC_BASE)


def test_removed_file_drops_manifest_item_without_scheduling_object_save() -> None:
    current = _initial_manifest()
    doc_item = next(item for item in current.items if item.logicalId == "doc_01")
    request = CommitPackRevisionInput(
        target=_target(current.versionId),
        operation="remove_file",
        removedFiles=[
            RemovedPackFile(
                logicalId="doc_01",
                previousFileVersionId=doc_item.fileVersionId,
                reason="manual cleanup",
            )
        ],
    )

    result = build_revision_commit(current, request, now=NOW_2, public_base_url=PUBLIC_BASE)

    assert [item.logicalId for item in result.items] == ["quiz_01"]
    assert result.removedFiles == ["doc_01.json"]
    assert result.manifest.change.removedFileRefs[0].fileVersionId == doc_item.fileVersionId
    assert not result.docObjects
    assert not result.quizObjects


def test_changed_added_and_removed_can_coexist_in_one_commit() -> None:
    first = build_revision_commit(
        None,
        CommitPackRevisionInput(
            target=_target(),
            operation="initial_generate",
            addedFiles=[_added_doc("doc_01"), _added_doc("doc_02"), _added_quiz("quiz_01")],
        ),
        now=NOW_1,
        public_base_url=PUBLIC_BASE,
    ).manifest
    doc_01 = next(item for item in first.items if item.logicalId == "doc_01")
    doc_02 = next(item for item in first.items if item.logicalId == "doc_02")
    request = CommitPackRevisionInput(
        target=_target(first.versionId),
        operation="manual_admin",
        changedFiles=[_doc_file("doc_01", previous=doc_01.fileVersionId)],
        addedFiles=[_added_quiz("quiz_02")],
        removedFiles=[RemovedPackFile(logicalId="doc_02", previousFileVersionId=doc_02.fileVersionId)],
        changedUnits=[ChangedUnit(fileName="doc_01.json", unitId="doc-1", fields=["text"], category="style")],
        newAudioObjects=[
            AudioObject(
                audioVersionId="av_20260613_100200_doc_01__doc-1_abcd1234",
                relativePath="objects/audio/av_20260613_100200_doc_01__doc-1_abcd1234.mp3",
                data=b"mp3",
            )
        ],
    )

    result = build_revision_commit(first, request, now=NOW_3, public_base_url=PUBLIC_BASE)

    assert result.revision == 2
    assert result.sourceVersionId == first.versionId
    assert result.changedFiles == ["doc_01.json"]
    assert result.addedFiles == ["quiz_02.json"]
    assert result.removedFiles == ["doc_02.json"]
    assert [item.logicalId for item in result.items] == ["doc_01", "quiz_01", "quiz_02"]
    assert len(result.docObjects) == 1
    assert len(result.quizObjects) == 1
    assert len(result.audioObjects) == 1
    assert result.manifest.change.changedUnits[0].category == "style"


def test_overlapping_changed_added_removed_logical_ids_are_rejected() -> None:
    request = CommitPackRevisionInput(
        target=_target(),
        operation="manual_admin",
        changedFiles=[_doc_file("doc_01", previous="fv_previous")],
        addedFiles=[_added_doc("doc_01")],
    )

    with pytest.raises(RevisionCommitError, match="must be disjoint"):
        build_revision_commit(_initial_manifest(), request, now=NOW_2, public_base_url=PUBLIC_BASE)


def test_unsafe_pack_file_name_is_rejected_before_commit() -> None:
    request = CommitPackRevisionInput(
        target=_target(),
        operation="initial_generate",
        addedFiles=[
            AddedPackFile(
                name="../doc_01.json",
                kind="document",
                logicalId="doc_01",
                content={"id": "doc_01"},
            )
        ],
    )

    with pytest.raises(Exception):
        build_revision_commit(None, request, now=NOW_1, public_base_url=PUBLIC_BASE)
