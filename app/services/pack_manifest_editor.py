from app.schemas.pack_v2 import PackLatestV2, PackManifestV2, RemovedPackFile, RevisionTarget, CommitPackRevisionInput
from app.schemas.request import EditPackManifestRequest, EditPackManifestResponse
from app.services.pack_paths import pack_root_prefix
from app.services.revision_store import persist_revision_commit
from app.services.storage_client import StorageClient


def edit_pack_manifest(request: EditPackManifestRequest) -> EditPackManifestResponse:
    storage = StorageClient()
    prefix = pack_root_prefix(request.creatorId, request.contentId)
    latest_data = storage.read_latest(prefix)
    if latest_data is None:
        raise FileNotFoundError("manifest not found")
    latest = PackLatestV2.model_validate(latest_data)
    if latest.versionId != request.versionId:
        raise ValueError("the manifest has changed; refresh and try again")
    manifest = PackManifestV2.model_validate(storage.read_manifest(prefix, latest.versionId))
    existing_ids = [item.logicalId for item in manifest.items]
    removed_ids = set(request.removedLogicalIds)
    if len(removed_ids) != len(request.removedLogicalIds) or not removed_ids.issubset(existing_ids):
        raise ValueError("removedLogicalIds must reference unique existing files")
    remaining_ids = set(existing_ids) - removed_ids
    if len(request.itemOrder) != len(set(request.itemOrder)) or set(request.itemOrder) != remaining_ids:
        raise ValueError("itemOrder must include every remaining file exactly once")
    result = persist_revision_commit(
        storage,
        manifest,
        CommitPackRevisionInput(
            target=RevisionTarget(creatorId=request.creatorId, contentId=request.contentId, versionId=manifest.versionId),
            operation="manual_admin",
            removedFiles=[RemovedPackFile(logicalId=item.logicalId, name=item.name, kind=item.kind, previousFileVersionId=item.fileVersionId, reason="removed_from_manifest") for item in manifest.items if item.logicalId in removed_ids],
            itemOrder=request.itemOrder,
            note="Reordered manifest items and removed selected files",
        ),
    )
    return EditPackManifestResponse(manifest=result.manifest)
