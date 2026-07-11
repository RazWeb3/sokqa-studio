"""List generated document and quiz packs from the configured storage backend."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas.pack_v2 import PackLatestV2, PackManifestV2
from app.services.pack_paths import pack_root_prefix, validate_safe_token
from app.services.storage_client import StorageClient

logger = logging.getLogger(__name__)


def list_generated_packs(creator_id: str | None = None) -> list[dict[str, Any]]:
    return _list_packs_from_storage(creator_id)


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
    return _list_packs_from_storage(creator_id)


def _list_gcs_packs(creator_id: str | None) -> list[dict[str, Any]]:
    return _list_packs_from_storage(creator_id)


def _list_packs_from_storage(creator_id: str | None) -> list[dict[str, Any]]:
    started = time.perf_counter()
    storage_client = StorageClient()
    client_ready_ms = _elapsed_ms(started)
    prefix_started = time.perf_counter()
    prefixes = storage_client.list_pack_prefixes_for_creator(creator_id)
    prefix_ms = _elapsed_ms(prefix_started)
    items: list[dict[str, Any]] = []
    latest_total_ms = 0
    latest_count = 0
    latest_bytes_total = 0
    latest_bytes_without_items_total = 0
    latest_items_total = 0
    latest_parse_ms_total = 0
    fallback_count = 0
    fallback_total_ms = 0
    for prefix in prefixes:
        identity = _identity_from_pack_prefix(prefix)
        if identity is None:
            continue
        blob_creator_id, content_id = identity
        latest_started = time.perf_counter()
        pack_items = _list_latest_items_for_content(storage_client, blob_creator_id, content_id)
        latest_elapsed = _elapsed_ms(latest_started)
        latest_total_ms += latest_elapsed
        latest_count += 1
        latest_meta = _latest_measurements(storage_client, pack_root_prefix(blob_creator_id, content_id))
        latest_bytes_total += latest_meta["bytes"]
        latest_bytes_without_items_total += latest_meta["bytesWithoutItems"]
        latest_items_total += latest_meta["items"]
        latest_parse_ms_total += latest_meta["parseMs"]
        if not pack_items:
            fallback_started = time.perf_counter()
            pack_items = _list_v2_items_for_content(
                storage_client,
                blob_creator_id,
                content_id,
                backfill_latest=True,
            )
            fallback_elapsed = _elapsed_ms(fallback_started)
            fallback_total_ms += fallback_elapsed
            fallback_count += 1
        items.extend(pack_items)
    sorted_items = sorted(items, key=_pack_sort_key)
    logger.info(
        "packs.list timing creatorId=%s storage=%s client_ready_ms=%s prefix_listing_ms=%s pack_count=%s "
        "latest_reads=%s latest_total_ms=%s latest_avg_ms=%.1f latest_bytes_total=%s latest_avg_bytes=%.1f "
        "latest_bytes_without_items_total=%s latest_items_bytes_estimate=%s latest_parse_ms_total=%s "
        "latest_items_total=%s latest_avg_items=%.1f fallback_count=%s fallback_total_ms=%s total_ms=%s item_count=%s",
        creator_id,
        get_settings().storage_backend,
        client_ready_ms,
        prefix_ms,
        len(prefixes),
        latest_count,
        latest_total_ms,
        (latest_total_ms / latest_count) if latest_count else 0.0,
        latest_bytes_total,
        (latest_bytes_total / latest_count) if latest_count else 0.0,
        latest_bytes_without_items_total,
        max(0, latest_bytes_total - latest_bytes_without_items_total),
        latest_parse_ms_total,
        latest_items_total,
        (latest_items_total / latest_count) if latest_count else 0.0,
        fallback_count,
        fallback_total_ms,
        _elapsed_ms(started),
        len(sorted_items),
    )
    return sorted_items


def _identity_from_pack_prefix(prefix: str) -> tuple[str, str] | None:
    parts = prefix.strip("/").split("/")
    base_parts = _storage_base_prefix().split("/")
    tail = parts[len(base_parts) :]
    if parts[: len(base_parts)] != base_parts:
        return None
    if len(tail) != 4 or tail[0] != "creators" or tail[2] != "packs":
        return None
    return tail[1], tail[3]


def _list_latest_items_for_content(storage: StorageClient, creator_id: str, content_id: str) -> list[dict[str, Any]]:
    try:
        validate_safe_token(creator_id)
        validate_safe_token(content_id)
        prefix = pack_root_prefix(creator_id, content_id)
        latest_data = storage.read_latest(prefix)
        if latest_data is None:
            return []
        latest = PackLatestV2.model_validate(latest_data)
    except Exception:
        return []
    return [_pack_item_from_latest_item(latest, item, creator_id, content_id, prefix) for item in latest.items]


def _latest_measurements(storage: StorageClient, prefix: str) -> dict[str, int]:
    raw = getattr(storage, "last_latest_measurement", {}).get(prefix, {})
    return {
        "bytes": int(raw.get("bytes") or 0),
        "bytesWithoutItems": int(raw.get("bytesWithoutItems") or 0),
        "items": int(raw.get("items") or 0),
        "parseMs": int(raw.get("parseMs") or 0),
    }


def _list_v2_items_for_content(
    storage: StorageClient,
    creator_id: str,
    content_id: str,
    *,
    backfill_latest: bool = False,
) -> list[dict[str, Any]]:
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
            if manifest_data.get("schemaVersion") != 1:
                continue
            manifest = PackManifestV2.model_validate(manifest_data)
        except Exception:
            continue
        if backfill_latest:
            _backfill_latest_from_manifest(storage, manifest, creator_id, prefix)
        return [_pack_item_from_v2_manifest_item(manifest, item, creator_id, content_id, prefix) for item in manifest.items]
    return []


def _backfill_latest_from_manifest(
    storage: StorageClient,
    manifest: PackManifestV2,
    creator_id: str,
    prefix: str,
) -> None:
    try:
        base_url = get_settings().public_base_url.rstrip("/")
        latest = PackLatestV2(
            creatorId=creator_id,
            contentId=manifest.contentId,
            storagePrefix=prefix,
            versionId=manifest.versionId,
            revision=manifest.revision,
            manifestUrl=f"{base_url}/{prefix}/versions/{manifest.versionId}/manifest.json",
            assetBaseUrl=f"{base_url}/{prefix}",
            title=manifest.title,
            description=manifest.description,
            slug=manifest.slug,
            language=manifest.language,
            generatedAt=manifest.generatedAt,
            change=manifest.change,
            items=manifest.items,
        )
        storage.save_latest(prefix, latest.model_dump(mode="json", exclude_none=True))
        logger.info(
            "packs.latest backfilled creatorId=%s contentId=%s versionId=%s revision=%s items=%s",
            creator_id,
            manifest.contentId,
            manifest.versionId,
            manifest.revision,
            len(manifest.items),
        )
    except Exception as exc:
        logger.warning(
            "failed to backfill pack latest pointer for creatorId=%s contentId=%s versionId=%s: %s",
            creator_id,
            manifest.contentId,
            manifest.versionId,
            exc,
        )


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
        "qualityStatus": manifest.qualityStatus,
        "publicationStatus": manifest.publicationStatus,
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


def _pack_item_from_latest_item(
    latest: PackLatestV2,
    item,
    creator_id: str,
    content_id: str,
    storage_prefix: str,
) -> dict[str, Any]:
    return {
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": latest.versionId,
        "revision": latest.revision,
        "schemaVersion": 2,
        "packName": item.name,
        "kind": item.kind,
        "logicalId": item.logicalId,
        "fileVersionId": item.fileVersionId,
        "title": item.title or item.name,
        "manifestTitle": latest.title,
        "manifestUrl": latest.manifestUrl,
        "url": item.url,
        "assetBaseUrl": latest.assetBaseUrl,
        "storagePrefix": storage_prefix,
        "items": [
            {
                "kind": latest_item.kind,
                "name": latest_item.name,
                "title": latest_item.title or latest_item.name,
                "logicalId": latest_item.logicalId,
                "fileVersionId": latest_item.fileVersionId,
            }
            for latest_item in latest.items
        ],
        "target": {
            "creatorId": creator_id,
            "contentId": content_id,
            "versionId": latest.versionId,
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


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
