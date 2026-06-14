"""List generated document and quiz packs from the configured storage backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.cloud import storage

from app.config import get_settings
from app.schemas.pack_v2 import PackManifestV2
from app.services.pack_paths import pack_root_prefix, validate_safe_token
from app.services.storage_client import StorageClient


def list_generated_packs(creator_id: str | None = None) -> list[dict[str, Any]]:
    settings = get_settings()
    if settings.storage_backend == "gcs":
        return _list_gcs_packs(creator_id)
    return _list_local_packs(creator_id)


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


def _list_local_packs(creator_id: str | None) -> list[dict[str, Any]]:
    creators_root = _local_storage_root() / _storage_base_prefix() / "creators"
    if not creators_root.exists():
        return []

    creator_dirs = [creators_root / creator_id] if creator_id else sorted(creators_root.iterdir())
    storage = StorageClient()
    items: list[dict[str, Any]] = []
    for creator_dir in creator_dirs:
        packs_root = creator_dir / "packs"
        if not packs_root.is_dir():
            continue
        for content_dir in sorted(path for path in packs_root.iterdir() if path.is_dir()):
            v2_items = _list_v2_items_for_content(storage, creator_dir.name, content_dir.name)
            if v2_items:
                items.extend(v2_items)
    return sorted(items, key=_pack_sort_key)


def _list_gcs_packs(creator_id: str | None) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.gcs_bucket:
        raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")

    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    base = _storage_base_prefix()
    prefix = f"{base}/creators/{creator_id}/" if creator_id else f"{base}/creators/"
    storage_client = StorageClient()
    blobs = list(bucket.list_blobs(prefix=prefix))
    content_keys = _list_gcs_content_keys(blobs, base, creator_id)
    items: list[dict[str, Any]] = []
    for blob_creator_id, content_id in sorted(content_keys):
        v2_items = _list_v2_items_for_content(storage_client, blob_creator_id, content_id)
        if v2_items:
            items.extend(v2_items)
    return sorted(items, key=_pack_sort_key)


def _list_gcs_content_keys(blobs, base: str, creator_id: str | None) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for blob in blobs:
        parts = blob.name.strip("/").split("/")
        base_parts = base.split("/")
        tail = parts[len(base_parts) :]
        if len(tail) < 4:
            continue
        if tail[0] != "creators" or tail[2] != "packs":
            continue
        if creator_id and tail[1] != creator_id:
            continue
        keys.add((tail[1], tail[3]))
    return keys


def _list_v2_items_for_content(storage: StorageClient, creator_id: str, content_id: str) -> list[dict[str, Any]]:
    try:
        validate_safe_token(creator_id)
        validate_safe_token(content_id)
        prefix = pack_root_prefix(creator_id, content_id)
    except ValueError:
        return []

    manifest_paths = sorted(
        storage.list_manifests(prefix),
        key=lambda path: _version_id_from_manifest_path(path) or "",
        reverse=True,
    )
    for manifest_path in manifest_paths:
        version_id = _version_id_from_manifest_path(manifest_path)
        if not version_id:
            continue
        try:
            manifest_data = storage.read_manifest(prefix, version_id)
            if manifest_data.get("schemaVersion") != 2:
                continue
            manifest = PackManifestV2.model_validate(manifest_data)
        except Exception:
            continue
        return [_pack_item_from_v2_manifest_item(manifest, item, creator_id, content_id, prefix) for item in manifest.items]
    return []


def _version_id_from_manifest_path(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) != 3 or parts[0] != "versions" or parts[2] != "manifest.json":
        return None
    return parts[1]


def _pack_item_from_v2_manifest_item(
    manifest: PackManifestV2,
    item,
    creator_id: str,
    content_id: str,
    storage_prefix: str,
) -> dict[str, Any]:
    base_url = get_settings().public_base_url.rstrip("/")
    manifest_url = f"{base_url}/{storage_prefix}/versions/{manifest.versionId}/manifest.json"
    return {
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": manifest.versionId,
        "revision": manifest.revision,
        "schemaVersion": 2,
        "packName": item.name,
        "kind": item.kind,
        "logicalId": item.logicalId,
        "fileVersionId": item.fileVersionId,
        "title": item.title or item.name,
        "manifestTitle": manifest.title,
        "manifestUrl": manifest_url,
        "url": item.url,
        "assetBaseUrl": f"{base_url}/{storage_prefix}",
        "storagePrefix": storage_prefix,
        "items": [
            {
                "kind": manifest_item.kind,
                "name": manifest_item.name,
                "title": manifest_item.title or manifest_item.name,
                "logicalId": manifest_item.logicalId,
                "fileVersionId": manifest_item.fileVersionId,
            }
            for manifest_item in manifest.items
        ],
        "target": {
            "creatorId": creator_id,
            "contentId": content_id,
            "versionId": manifest.versionId,
            "packName": item.name,
            "kind": item.kind,
        },
    }


def _pack_sort_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item["creatorId"]),
        str(item["contentId"]),
        str(item["versionId"]),
        str(item["packName"]),
    )
