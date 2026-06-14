from __future__ import annotations

import json
from datetime import datetime

from app.schemas.pack_v2 import CommitPackRevisionInput, PackManifestV2, RevisionCommitResult
from app.services.pack_paths import pack_root_prefix
from app.services.revision_commit import build_revision_commit
from app.services.storage_client import StorageClient


def persist_revision_commit(
    storage: StorageClient,
    current_manifest: PackManifestV2 | None,
    request: CommitPackRevisionInput,
    *,
    now: datetime | None = None,
    public_base_url: str | None = None,
) -> RevisionCommitResult:
    result = build_revision_commit(
        current_manifest,
        request,
        now=now,
        public_base_url=public_base_url,
    )
    prefix = pack_root_prefix(request.target.creatorId, request.target.contentId)

    for obj in [*result.docObjects, *result.quizObjects]:
        storage.save_object(
            prefix,
            obj.relativePath,
            json.dumps(obj.content, ensure_ascii=False, indent=2),
            "application/json; charset=utf-8",
        )

    for audio in result.audioObjects:
        storage.save_object(
            prefix,
            audio.relativePath,
            audio.data,
            audio.contentType,
        )

    storage.save_manifest(
        prefix,
        result.versionId,
        result.manifest.model_dump(mode="json", exclude_none=True),
    )
    return result


def read_pack_manifest_v2(storage: StorageClient, creator_id: str, content_id: str, version_id: str) -> PackManifestV2:
    prefix = pack_root_prefix(creator_id, content_id)
    return PackManifestV2.model_validate(storage.read_manifest(prefix, version_id))
