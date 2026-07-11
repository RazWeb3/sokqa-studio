from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.config import get_settings
from app.schemas.pack_v2 import (
    AddedPackFile,
    AudioObject,
    ChangedPackFile,
    CommitPackRevisionInput,
    ManifestChange,
    ManifestCreatorV2,
    ManifestItemV2,
    PackManifestV2,
    PackObjectToSave,
    RemovedFileRef,
    RemovedPackFile,
    RevisionCommitResult,
)
from app.services.pack_paths import (
    asset_base_url,
    doc_object_relative_path,
    generate_build_id,
    generate_file_version_id,
    generate_version_id,
    manifest_relative_path,
    pack_root_prefix,
    quiz_object_relative_path,
    resolve_asset_url,
    validate_relative_path,
    validate_safe_token,
)


class RevisionCommitError(ValueError):
    pass


def build_revision_commit(
    current_manifest: PackManifestV2 | None,
    request: CommitPackRevisionInput,
    *,
    now: datetime | None = None,
    public_base_url: str | None = None,
) -> RevisionCommitResult:
    _validate_target(current_manifest, request)
    _validate_disjoint_file_operations(request)

    existing_items = _items_by_logical_id(current_manifest.items if current_manifest else [])
    items = dict(existing_items)
    removed_refs: list[RemovedFileRef] = []

    for removed in request.removedFiles:
        item = _pop_removed_item(items, removed)
        removed_refs.append(
            RemovedFileRef(
                logicalId=removed.logicalId,
                fileVersionId=item.fileVersionId,
                reason=removed.reason,
            )
        )

    asset_base = asset_base_url(
        public_base_url or get_settings().public_base_url,
        request.target.creatorId,
        request.target.contentId,
    )
    doc_objects: list[PackObjectToSave] = []
    quiz_objects: list[PackObjectToSave] = []

    for changed in request.changedFiles:
        previous = items.get(changed.logicalId)
        if previous is None:
            raise RevisionCommitError(f"changed logicalId does not exist: {changed.logicalId}")
        if previous.fileVersionId != changed.previousFileVersionId:
            raise RevisionCommitError(f"previousFileVersionId mismatch for {changed.logicalId}")
        item, obj = _item_and_object_for_file(changed, asset_base, now=now)
        items[changed.logicalId] = item
        _append_object(obj, doc_objects, quiz_objects)

    for added in request.addedFiles:
        if added.logicalId in items:
            raise RevisionCommitError(f"added logicalId already exists: {added.logicalId}")
        item, obj = _item_and_object_for_file(added, asset_base, now=now)
        items[added.logicalId] = item
        _append_object(obj, doc_objects, quiz_objects)

    source_version_id = current_manifest.versionId if current_manifest else None
    revision = (current_manifest.revision + 1) if current_manifest else 1
    version_id = generate_version_id(now)
    build_id = generate_build_id(version_id)
    generated_at = (now or datetime.now(timezone(timedelta(hours=9)))).isoformat(timespec="seconds")
    item_list = _ordered_items(current_manifest.items if current_manifest else [], items, request.addedFiles)
    change = ManifestChange(
        operation=request.operation,
        changedFiles=[file.name for file in request.changedFiles],
        addedFiles=[file.name for file in request.addedFiles],
        removedFiles=[_removed_file_name(file, existing_items) for file in request.removedFiles],
        changedUnits=request.changedUnits,
        reRecordNeededUnits=request.reRecordNeededUnits,
        removedFileRefs=removed_refs,
        note=request.note,
    )
    manifest = PackManifestV2(
        id=f"{validate_safe_token(request.target.contentId)}_manifest_r{revision}",
        contentId=request.target.contentId,
        slug=current_manifest.slug if current_manifest else request.slug,
        title=current_manifest.title if current_manifest else request.title,
        description=current_manifest.description if current_manifest else (request.description or ""),
        language=current_manifest.language if current_manifest else (request.language or "ja"),
        author=current_manifest.author if current_manifest else request.author,
        scale=current_manifest.scale if current_manifest else request.scale,
        globalTags=current_manifest.globalTags if current_manifest else (request.globalTags or []),
        creator=(
            current_manifest.creator
            if current_manifest
            else ManifestCreatorV2(
                id=validate_safe_token(request.target.creatorId),
                displayName=request.creatorDisplayName,
            )
        ),
        revision=revision,
        versionId=version_id,
        sourceVersionId=source_version_id,
        buildId=build_id,
        generatedAt=generated_at,
        change=change,
        qualityStatus=request.qualityStatus or (current_manifest.qualityStatus if current_manifest else "valid"),
        publicationStatus=request.publicationStatus or (current_manifest.publicationStatus if current_manifest else "draft"),
        items=item_list,
    )
    manifest_url = resolve_asset_url(asset_base, manifest_relative_path(version_id))
    return RevisionCommitResult(
        contentId=request.target.contentId,
        revision=revision,
        versionId=version_id,
        sourceVersionId=source_version_id,
        manifestUrl=manifest_url,
        assetBaseUrl=asset_base,
        items=item_list,
        changedFiles=change.changedFiles,
        addedFiles=change.addedFiles,
        removedFiles=change.removedFiles,
        reRecordNeededUnits=request.reRecordNeededUnits,
        manifest=manifest,
        docObjects=doc_objects,
        quizObjects=quiz_objects,
        audioObjects=[_validated_audio_object(audio) for audio in request.newAudioObjects],
    )


def _validate_target(current_manifest: PackManifestV2 | None, request: CommitPackRevisionInput) -> None:
    validate_safe_token(request.target.creatorId)
    validate_safe_token(request.target.contentId)
    if request.target.versionId:
        validate_safe_token(request.target.versionId)
    pack_root_prefix(request.target.creatorId, request.target.contentId)
    if current_manifest and current_manifest.contentId != request.target.contentId:
        raise RevisionCommitError("target contentId does not match current manifest")
    if current_manifest and current_manifest.creator.id != request.target.creatorId:
        raise RevisionCommitError("target creatorId does not match current manifest")
    if current_manifest and request.target.versionId and current_manifest.versionId != request.target.versionId:
        raise RevisionCommitError("target versionId does not match current manifest")


def _validate_disjoint_file_operations(request: CommitPackRevisionInput) -> None:
    groups = [
        {file.logicalId for file in request.changedFiles},
        {file.logicalId for file in request.addedFiles},
        {file.logicalId for file in request.removedFiles},
    ]
    combined = set().union(*groups)
    if sum(len(group) for group in groups) != len(combined):
        raise RevisionCommitError("changedFiles, addedFiles, and removedFiles logicalIds must be disjoint")


def _items_by_logical_id(items: Iterable[ManifestItemV2]) -> dict[str, ManifestItemV2]:
    result: dict[str, ManifestItemV2] = {}
    for item in items:
        validate_safe_token(item.logicalId)
        validate_safe_token(item.fileVersionId)
        validate_safe_token(item.name)
        if item.logicalId in result:
            raise RevisionCommitError(f"duplicate logicalId: {item.logicalId}")
        result[item.logicalId] = item
    return result


def _pop_removed_item(items: dict[str, ManifestItemV2], removed: RemovedPackFile) -> ManifestItemV2:
    item = items.get(removed.logicalId)
    if item is None:
        raise RevisionCommitError(f"removed logicalId does not exist: {removed.logicalId}")
    if removed.previousFileVersionId and removed.previousFileVersionId != item.fileVersionId:
        raise RevisionCommitError(f"previousFileVersionId mismatch for removed {removed.logicalId}")
    if removed.name and removed.name != item.name:
        raise RevisionCommitError(f"name mismatch for removed {removed.logicalId}")
    if removed.kind and removed.kind != item.kind:
        raise RevisionCommitError(f"kind mismatch for removed {removed.logicalId}")
    return items.pop(removed.logicalId)


def _item_and_object_for_file(
    file: ChangedPackFile | AddedPackFile,
    asset_base: str,
    *,
    now: datetime | None,
) -> tuple[ManifestItemV2, PackObjectToSave]:
    validate_safe_token(file.name)
    validate_safe_token(file.logicalId)
    file_version_id = generate_file_version_id(file.kind, file.logicalId, now)
    relative_path = _object_relative_path(file.kind, file_version_id)
    content = copy.deepcopy(file.content)
    content["assetBaseUrl"] = asset_base
    content_hash = _content_hash(content)
    item = ManifestItemV2(
        kind=file.kind,
        name=file.name,
        title=_display_title_for_file(file.content, file.name),
        logicalId=file.logicalId,
        fileVersionId=file_version_id,
        url=resolve_asset_url(asset_base, relative_path),
        sizeBytes=len(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8")),
        contentHash=content_hash,
    )
    obj = PackObjectToSave(
        kind=file.kind,
        name=file.name,
        logicalId=file.logicalId,
        fileVersionId=file_version_id,
        relativePath=relative_path,
        content=content,
        contentHash=content_hash,
    )
    return item, obj


def _object_relative_path(kind: str, file_version_id: str) -> str:
    if kind == "document":
        return doc_object_relative_path(file_version_id)
    if kind == "quiz":
        return quiz_object_relative_path(file_version_id)
    raise RevisionCommitError(f"unsupported file kind: {kind}")


def _append_object(obj: PackObjectToSave, doc_objects: list[PackObjectToSave], quiz_objects: list[PackObjectToSave]) -> None:
    if obj.kind == "document":
        doc_objects.append(obj)
    else:
        quiz_objects.append(obj)


def _ordered_items(
    previous_items: list[ManifestItemV2],
    current_items: dict[str, ManifestItemV2],
    added_files: list[AddedPackFile],
) -> list[ManifestItemV2]:
    ordered: list[ManifestItemV2] = []
    seen: set[str] = set()
    for item in previous_items:
        replacement = current_items.get(item.logicalId)
        if replacement is not None:
            ordered.append(replacement)
            seen.add(item.logicalId)
    for file in added_files:
        if file.logicalId not in seen:
            ordered.append(current_items[file.logicalId])
            seen.add(file.logicalId)
    for logical_id in sorted(set(current_items) - seen):
        ordered.append(current_items[logical_id])
    return ordered


def _removed_file_name(file: RemovedPackFile, existing_items: dict[str, ManifestItemV2]) -> str:
    if file.name:
        return file.name
    item = existing_items.get(file.logicalId)
    return item.name if item else file.logicalId


def _display_title_for_file(content: dict[str, Any], fallback_name: str) -> str:
    title = content.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    return fallback_name


def _validated_audio_object(audio: AudioObject) -> AudioObject:
    validate_safe_token(audio.audioVersionId)
    validate_relative_path(audio.relativePath)
    return audio


def _content_hash(content: dict) -> str:
    payload = json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
