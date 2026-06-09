from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from google.cloud import storage

from app.config import get_settings
from app.schemas.request import DeletePackRequest, DeletePackResponse
from app.services.pack_metadata import pack_storage_prefix
from app.services.storage_status import record_storage_event


def delete_pack_version(request: DeletePackRequest) -> DeletePackResponse:
    storage_prefix = resolve_delete_storage_prefix(request)
    object_names = list_storage_objects(storage_prefix)
    if not object_names:
        raise FileNotFoundError(f"no objects found under storagePrefix: {storage_prefix}")
    deleted_count = delete_storage_objects(object_names)
    return DeletePackResponse(
        storagePrefix=storage_prefix,
        objectCount=len(object_names),
        deletedCount=deleted_count,
        objectNames=object_names,
    )


def resolve_delete_storage_prefix(request: DeletePackRequest) -> str:
    if request.storagePrefix:
        return validate_pack_version_prefix(request.storagePrefix)
    if request.manifestUrl:
        return validate_pack_version_prefix(_storage_prefix_from_manifest_url(request.manifestUrl))
    if request.creatorId and request.contentId and request.versionId:
        return validate_pack_version_prefix(pack_storage_prefix(request.creatorId, request.contentId, request.versionId))
    raise ValueError("delete target must include storagePrefix, manifestUrl, or creatorId/contentId/versionId")


def validate_pack_version_prefix(storage_prefix: str) -> str:
    prefix = storage_prefix.strip().strip("/")
    parts = prefix.split("/")
    base_parts = _storage_base_prefix().split("/")
    tail = parts[len(base_parts) :]
    if parts[: len(base_parts)] != base_parts:
        raise ValueError("storagePrefix is outside the configured sokqa base prefix")
    if len(tail) != 6:
        raise ValueError("storagePrefix must target a single pack version")
    if tail[0] != "creators" or tail[2] != "packs" or tail[4] != "versions":
        raise ValueError("storagePrefix must be sokqa/creators/{creatorId}/packs/{contentId}/versions/{versionId}")
    if not all(_safe_path_token(value) for value in (tail[1], tail[3], tail[5])):
        raise ValueError("storagePrefix contains an unsafe path token")
    return prefix


def list_storage_objects(storage_prefix: str) -> list[str]:
    settings = get_settings()
    if settings.storage_backend == "gcs":
        return _list_gcs_objects(storage_prefix)
    return _list_local_objects(storage_prefix)


def delete_storage_objects(object_names: list[str]) -> int:
    if not object_names:
        return 0
    settings = get_settings()
    if settings.storage_backend == "gcs":
        return _delete_gcs_objects(object_names)
    return _delete_local_objects(object_names)


def _storage_base_prefix() -> str:
    base = get_settings().gcs_prefix.strip("/") or "sokqa"
    if base == "sokqa/packs":
        base = "sokqa"
    return base


def _local_storage_root() -> Path:
    local_dir = Path(get_settings().local_storage_dir)
    if local_dir.is_absolute():
        return local_dir
    return Path.cwd() / local_dir


def _storage_prefix_from_manifest_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    public_path = urlparse(get_settings().public_base_url).path.strip("/")
    if public_path and path.startswith(f"{public_path}/"):
        path = path[len(public_path) + 1 :]
    if path.startswith("generated/"):
        path = path.removeprefix("generated/")
    if not path.endswith("/manifest.json"):
        raise ValueError("manifestUrl must end with manifest.json")
    return path.removesuffix("/manifest.json").strip("/")


def _safe_path_token(value: str) -> bool:
    if not value or value in {".", ".."}:
        return False
    return "/" not in value and "\\" not in value


def _list_local_objects(storage_prefix: str) -> list[str]:
    root = _local_storage_root().resolve()
    target_dir = (root / storage_prefix).resolve()
    if not _is_relative_to(target_dir, root) or target_dir == root:
        raise ValueError("resolved local delete path is outside local storage")
    if not target_dir.is_dir():
        return []
    return [
        f"{storage_prefix}/{path.relative_to(target_dir).as_posix()}"
        for path in sorted(target_dir.rglob("*"))
        if path.is_file()
    ]


def _delete_local_objects(object_names: list[str]) -> int:
    root = _local_storage_root().resolve()
    deleted = 0
    touched_dirs: set[Path] = set()
    for object_name in object_names:
        target = (root / object_name).resolve()
        if not _is_relative_to(target, root) or target == root:
            raise ValueError("resolved local object path is outside local storage")
        if target.is_file():
            target.unlink()
            touched_dirs.add(target.parent)
            deleted += 1
            record_storage_event(f"local deleted: {object_name}")
    for directory in sorted(touched_dirs, key=lambda path: len(path.parts), reverse=True):
        while _is_relative_to(directory, root) and directory != root:
            try:
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent
    return deleted


def _list_gcs_objects(storage_prefix: str) -> list[str]:
    settings = get_settings()
    if not settings.gcs_bucket:
        raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    prefix = f"{storage_prefix.rstrip('/')}/"
    return sorted(blob.name for blob in bucket.list_blobs(prefix=prefix))


def _delete_gcs_objects(object_names: list[str]) -> int:
    settings = get_settings()
    if not settings.gcs_bucket:
        raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")
    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    deleted = 0
    for object_name in object_names:
        bucket.blob(object_name).delete()
        deleted += 1
        record_storage_event(f"gcs deleted: {object_name}")
    return deleted


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
