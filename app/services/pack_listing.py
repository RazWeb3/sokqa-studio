"""List generated document and quiz packs from the configured storage backend."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.cloud import storage

from app.config import get_settings


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
    items: list[dict[str, Any]] = []
    for creator_dir in creator_dirs:
        packs_root = creator_dir / "packs"
        if not packs_root.is_dir():
            continue
        for content_dir in sorted(path for path in packs_root.iterdir() if path.is_dir()):
            versions_root = content_dir / "versions"
            if not versions_root.is_dir():
                continue
            for version_dir in sorted(path for path in versions_root.iterdir() if path.is_dir()):
                storage_prefix = _relative_prefix_from_version_dir(version_dir, creator_dir.name, content_dir.name)
                manifest_content = _read_local_manifest(version_dir)
                if manifest_content is None:
                    continue
                for path in sorted(version_dir.glob("*.json")):
                    item = _pack_item_from_content_path(
                        path,
                        creator_dir.name,
                        content_dir.name,
                        version_dir.name,
                        storage_prefix,
                        manifest_content,
                    )
                    if item:
                        items.append(item)
    return _latest_version_items(items)


def _relative_prefix_from_version_dir(version_dir: Path, creator_id: str, content_id: str) -> str:
    return f"{_storage_base_prefix()}/creators/{creator_id}/packs/{content_id}/versions/{version_dir.name}"


def _pack_item_from_content_path(
    path: Path,
    creator_id: str,
    content_id: str,
    version_id: str,
    storage_prefix: str,
    manifest_content: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    try:
        content = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _pack_item_from_content(content, path.name, creator_id, content_id, version_id, storage_prefix, manifest_content)


def _read_local_manifest(version_dir: Path) -> dict[str, Any] | None:
    try:
        return json.loads((version_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_gcs_packs(creator_id: str | None) -> list[dict[str, Any]]:
    settings = get_settings()
    if not settings.gcs_bucket:
        raise ValueError("GCS_BUCKET is required when STORAGE_BACKEND=gcs")

    client = storage.Client()
    bucket = client.bucket(settings.gcs_bucket)
    base = _storage_base_prefix()
    prefix = f"{base}/creators/{creator_id}/" if creator_id else f"{base}/creators/"
    items: list[dict[str, Any]] = []
    for blob in bucket.list_blobs(prefix=prefix):
        parsed = _parse_pack_blob_name(blob.name, base)
        if parsed is None:
            continue
        blob_creator_id, content_id, version_id, pack_name, storage_prefix = parsed
        manifest_content = _read_gcs_manifest(bucket, storage_prefix)
        if manifest_content is None:
            continue
        try:
            content = json.loads(blob.download_as_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        item = _pack_item_from_content(
            content,
            pack_name,
            blob_creator_id,
            content_id,
            version_id,
            storage_prefix,
            manifest_content,
        )
        if item:
            items.append(item)
    return _latest_version_items(items)


def _read_gcs_manifest(bucket, storage_prefix: str) -> dict[str, Any] | None:
    try:
        return json.loads(bucket.blob(f"{storage_prefix}/manifest.json").download_as_text(encoding="utf-8"))
    except Exception:
        return None


def _parse_pack_blob_name(blob_name: str, base: str) -> tuple[str, str, str, str, str] | None:
    parts = blob_name.strip("/").split("/")
    base_parts = base.split("/")
    tail = parts[len(base_parts) :]
    if len(tail) != 7:
        return None
    if tail[0] != "creators" or tail[2] != "packs" or tail[4] != "versions":
        return None
    creator_id, content_id, version_id, pack_name = tail[1], tail[3], tail[5], tail[6]
    if not pack_name.endswith(".json"):
        return None
    storage_prefix = "/".join(parts[:-1])
    return creator_id, content_id, version_id, pack_name, storage_prefix


def _pack_item_from_content(
    content: dict[str, Any],
    pack_name: str,
    creator_id: str,
    content_id: str,
    version_id: str,
    storage_prefix: str,
    manifest_content: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    kind = content.get("type")
    if kind not in {"document", "quiz"}:
        return None
    base_url = get_settings().public_base_url.rstrip("/")
    url = f"{base_url}/{storage_prefix}/{pack_name}"
    manifest_url = f"{base_url}/{storage_prefix}/manifest.json"
    return {
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": version_id,
        "packName": pack_name,
        "kind": kind,
        "title": content.get("title") or content.get("id") or pack_name,
        "manifestTitle": (manifest_content or {}).get("title"),
        "manifestUrl": manifest_url,
        "url": url,
        "assetBaseUrl": content.get("assetBaseUrl"),
        "storagePrefix": storage_prefix,
        "target": {
            "creatorId": creator_id,
            "contentId": content_id,
            "versionId": version_id,
            "packName": pack_name,
            "kind": kind,
        },
    }


def _pack_sort_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(item["creatorId"]),
        str(item["contentId"]),
        str(item["versionId"]),
        str(item["packName"]),
    )


def _latest_version_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest_versions: dict[tuple[str, str], str] = {}
    for item in items:
        key = (str(item["creatorId"]), str(item["contentId"]))
        version_id = str(item["versionId"])
        if version_id > latest_versions.get(key, ""):
            latest_versions[key] = version_id

    latest_items = [
        item
        for item in items
        if str(item["versionId"]) == latest_versions[(str(item["creatorId"]), str(item["contentId"]))]
    ]
    return sorted(latest_items, key=_pack_sort_key)
