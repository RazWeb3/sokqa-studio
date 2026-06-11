"""API-facing helpers for synchronous TTS recording."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from app.config import get_settings
from app.schemas.request import TtsRecordingTarget
from app.schemas.sokqa import GeneratedFile, PackManifest, SokqaDocumentPack, SokqaQuizPack
from app.services.pack_metadata import build_pack_version_metadata, pack_storage_prefix
from app.services.storage_client import StorageClient
from app.services.tts_estimation import RecordingTextSource, RecordingUnit, extract_recording_units
from app.services.tts_recorder import RecordingSummary, Synthesizer, clear_pack_audio_urls, record_generated_file_audio


def estimate_recording(
    target: TtsRecordingTarget,
    unit_ids: list[str] | None = None,
    text_source: RecordingTextSource = "raw",
) -> dict:
    loaded = load_target_pack(target)
    units = _select_units(extract_recording_units(loaded.pack, text_source), unit_ids, include_recorded=True)
    billable_units = [unit for unit in units if not unit.is_recorded]
    return _estimate_response(loaded, units, billable_units, text_source)


def run_recording(
    target: TtsRecordingTarget,
    unit_ids: list[str],
    text_source: RecordingTextSource = "raw",
    force_rerecord: bool = False,
    *,
    language_code: str | None = None,
    voice_name: str | None = None,
    speaking_rate: float | None = None,
    pitch: float | None = None,
    storage_client: StorageClient | None = None,
    synthesize_fn: Synthesizer | None = None,
) -> dict:
    max_units = get_settings().cloud_tts_recording_request_max_units
    if len(unit_ids) > max_units:
        raise ValueError(f"unitIds exceeds the per-request limit: {max_units}")

    loaded = load_target_pack(target)
    creator_id, content_id, _ = _identity_from_storage_prefix(loaded.storage_prefix)
    storage = storage_client or StorageClient()
    all_units = extract_recording_units(loaded.pack, text_source)
    selected = _select_units(all_units, unit_ids, include_recorded=True)
    recording_targets = [unit for unit in selected if force_rerecord or not unit.is_recorded]
    version_metadata = build_pack_version_metadata(creator_id, content_id) if recording_targets else None
    recording_storage_prefix = version_metadata.storage_prefix if version_metadata else loaded.storage_prefix
    if version_metadata:
        storage.copy_prefix(loaded.storage_prefix, version_metadata.storage_prefix)
    kwargs = {"storage_client": storage}
    if synthesize_fn is not None:
        kwargs["synthesize_fn"] = synthesize_fn
    summary = record_generated_file_audio(
        loaded.file,
        selected,
        recording_storage_prefix,
        force_rerecord=force_rerecord,
        language_code=language_code,
        voice_name=voice_name,
        speaking_rate=speaking_rate,
        pitch=pitch,
        **kwargs,
    )
    loaded.pack = _pack_from_file(loaded.file)
    response_storage_prefix = recording_storage_prefix
    response_version_id = version_metadata.version_id if version_metadata else _identity_from_storage_prefix(response_storage_prefix)[2]
    asset_base_url = loaded.pack.assetBaseUrl or _public_url_for_prefix(storage, response_storage_prefix)
    pack_url = f"{asset_base_url.rstrip('/')}/{loaded.file.name}"
    if version_metadata:
        _complete_recording_snapshot(
            storage,
            creator_id,
            content_id,
            loaded.storage_prefix,
            version_metadata.storage_prefix,
            version_metadata.version_id,
            version_metadata.build_id,
            version_metadata.generated_at,
            loaded.file.name,
        )
    return {
        "packId": loaded.pack.id,
        "packType": loaded.pack.type,
        "packName": loaded.file.name,
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": response_version_id,
        "buildId": version_metadata.build_id if version_metadata else None,
        "generatedAt": version_metadata.generated_at if version_metadata else None,
        "storagePrefix": response_storage_prefix,
        "assetBaseUrl": asset_base_url,
        "packUrl": pack_url,
        "target": {
            "creatorId": creator_id,
            "contentId": content_id,
            "versionId": response_version_id,
            "packName": loaded.file.name,
            "kind": loaded.file.kind,
        },
        "textSource": text_source,
        "forceRerecord": force_rerecord,
        "summary": _summary_to_dict(summary),
        "audioUrls": [
            {
                "unitId": result.unit_id,
                "audioUrl": result.audio_url,
                "audioPath": result.audio_path,
                "usedTextSource": result.used_text_source,
            }
            for result in summary.results
            if result.success and result.audio_url
        ],
    }


def reset_recording(
    target: TtsRecordingTarget,
    unit_ids: list[str] | None = None,
    text_source: RecordingTextSource = "raw",
) -> dict:
    loaded = load_target_pack(target)
    all_units = extract_recording_units(loaded.pack, text_source)
    selected = _select_units(all_units, unit_ids, include_recorded=True)
    cleared_count = clear_pack_audio_urls(loaded.pack, selected)
    loaded.file.content = loaded.pack.model_dump(exclude_none=True)
    StorageClient().save_files(loaded.pack.id, [loaded.file], loaded.storage_prefix)
    refreshed_units = extract_recording_units(loaded.pack, text_source)
    affected_ids = {unit.item_id for unit in selected}
    return {
        "packId": loaded.pack.id,
        "packType": loaded.pack.type,
        "packName": loaded.file.name,
        "storagePrefix": loaded.storage_prefix,
        "assetBaseUrl": loaded.pack.assetBaseUrl,
        "textSource": text_source,
        "requestedUnitCount": len(selected),
        "clearedCount": cleared_count,
        "units": [_unit_to_dict(unit) for unit in refreshed_units if unit.item_id in affected_ids],
    }


class LoadedPack:
    def __init__(self, pack: SokqaDocumentPack | SokqaQuizPack, file: GeneratedFile, storage_prefix: str) -> None:
        self.pack = pack
        self.file = file
        self.storage_prefix = storage_prefix


def load_target_pack(target: TtsRecordingTarget) -> LoadedPack:
    if target.packUrl:
        pack_name = target.packName or _url_basename(target.packUrl)
        storage_prefix = _storage_prefix_from_pack_url(target.packUrl, pack_name)
        return _loaded_from_content(_load_json_url(target.packUrl), pack_name, storage_prefix)

    if target.manifestUrl:
        manifest = PackManifest.model_validate(_load_json_url(target.manifestUrl))
        item_url = _select_manifest_item_url(manifest, target)
        pack_name = target.packName or _url_basename(item_url)
        storage_prefix = _storage_prefix_from_pack_url(item_url, pack_name)
        return _loaded_from_content(_load_json_url(item_url), pack_name, storage_prefix)

    if target.creatorId and target.contentId and target.versionId and target.packName:
        storage_prefix = pack_storage_prefix(target.creatorId, target.contentId, target.versionId)
        content = StorageClient().load_json_file(target.contentId, target.packName, storage_prefix)
        return _loaded_from_content(content, target.packName, storage_prefix)

    raise ValueError("target must include packUrl, manifestUrl, or creatorId/contentId/versionId/packName")


def _load_json_url(url: str) -> dict:
    parsed = urlparse(url)
    settings = get_settings()
    if parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.startswith("/generated/"):
        relative = parsed.path.removeprefix("/generated/").lstrip("/")
        path = Path.cwd() / settings.local_storage_dir / relative
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    with urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _loaded_from_content(content: dict, file_name: str, storage_prefix: str) -> LoadedPack:
    pack = _pack_from_content(content)
    return LoadedPack(
        pack=pack,
        file=GeneratedFile(name=file_name, kind=pack.type, content=content),
        storage_prefix=storage_prefix,
    )


def _pack_from_content(content: dict) -> SokqaDocumentPack | SokqaQuizPack:
    if content.get("type") == "document":
        return SokqaDocumentPack.model_validate(content)
    if content.get("type") == "quiz":
        return SokqaQuizPack.model_validate(content)
    raise ValueError("target pack must be document or quiz")


def _pack_from_file(file: GeneratedFile) -> SokqaDocumentPack | SokqaQuizPack:
    return _pack_from_content(file.content)


def _select_manifest_item_url(manifest: PackManifest, target: TtsRecordingTarget) -> str:
    candidates = manifest.items
    if target.kind:
        candidates = [item for item in candidates if item.kind == target.kind]
    if target.packName:
        candidates = [item for item in candidates if _url_basename(item.url) == target.packName]
    if len(candidates) != 1:
        raise ValueError("manifest target is ambiguous; provide packName or kind")
    return candidates[0].url


def _select_units(units: list[RecordingUnit], unit_ids: list[str] | None, *, include_recorded: bool) -> list[RecordingUnit]:
    if unit_ids is None:
        return [unit for unit in units if include_recorded or not unit.is_recorded]
    by_id = {unit.item_id: unit for unit in units}
    missing = [unit_id for unit_id in unit_ids if unit_id not in by_id]
    if missing:
        raise ValueError(f"unknown unitIds: {', '.join(missing)}")
    return [by_id[unit_id] for unit_id in unit_ids if include_recorded or not by_id[unit_id].is_recorded]


def _estimate_response(
    loaded: LoadedPack,
    units: list[RecordingUnit],
    billable_units: list[RecordingUnit],
    text_source: RecordingTextSource,
) -> dict:
    total_chars = sum(unit.char_count for unit in billable_units)
    rate = get_settings().tts_credit_per_char
    return {
        "packId": loaded.pack.id,
        "packType": loaded.pack.type,
        "packName": loaded.file.name,
        "storagePrefix": loaded.storage_prefix,
        "textSource": text_source,
        "unitCount": len(units),
        "billableUnitCount": len(billable_units),
        "totalChars": total_chars,
        "estimatedCredits": total_chars * rate,
        "units": [_unit_to_dict(unit) for unit in units],
    }


def _unit_to_dict(unit: RecordingUnit) -> dict:
    return {
        "itemId": unit.item_id,
        "text": unit.text,
        "charCount": unit.char_count,
        "packId": unit.pack_id,
        "packType": unit.pack_type,
        "kind": unit.kind,
        "isRecorded": unit.is_recorded,
        "audioPath": unit.audio_path,
        "audioUrl": unit.audio_url,
        "hasCorrected": unit.has_corrected,
        "usedTextSource": unit.used_text_source,
    }


def _summary_to_dict(summary: RecordingSummary) -> dict:
    return {
        "totalUnits": summary.total_units,
        "skippedUnits": summary.skipped_units,
        "successCount": summary.success_count,
        "failureCount": summary.failure_count,
        "failedUnitIds": summary.failed_unit_ids,
        "results": [
            {
                "unitId": result.unit_id,
                "success": result.success,
                "audioUrl": result.audio_url,
                "audioPath": result.audio_path,
                "error": result.error,
                "usedTextSource": result.used_text_source,
            }
            for result in summary.results
        ],
    }


def _url_basename(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def _storage_prefix_from_pack_url(url: str, pack_name: str) -> str:
    path = urlparse(url).path.strip("/")
    if path.startswith("generated/"):
        path = path.removeprefix("generated/")
    if not path.endswith(pack_name):
        raise ValueError("pack URL does not end with packName")
    return path[: -len(pack_name)].rstrip("/")


def _identity_from_storage_prefix(storage_prefix: str) -> tuple[str, str, str]:
    parts = storage_prefix.strip("/").split("/")
    base = get_settings().gcs_prefix.strip("/") or "sokqa"
    if base == "sokqa/packs":
        base = "sokqa"
    base_parts = base.split("/")
    if parts[: len(base_parts)] != base_parts:
        raise ValueError("storagePrefix is outside the configured sokqa base prefix")
    tail = parts[len(base_parts) :]
    if len(tail) < 6 or tail[0] != "creators" or tail[2] != "packs" or tail[4] != "versions":
        raise ValueError("storagePrefix must be sokqa/creators/{creatorId}/packs/{contentId}/versions/{versionId}")
    return tail[1], tail[3], tail[5]


def _public_url_for_prefix(storage: StorageClient, storage_prefix: str) -> str:
    if hasattr(storage, "public_url_for_prefix"):
        return storage.public_url_for_prefix(storage_prefix)
    return f"{get_settings().public_base_url.rstrip('/')}/{storage_prefix.strip('/')}"


def _complete_recording_snapshot(
    storage: StorageClient,
    creator_id: str,
    content_id: str,
    source_prefix: str,
    target_prefix: str,
    version_id: str,
    build_id: str,
    generated_at: str,
    updated_pack_name: str,
) -> None:
    try:
        manifest = PackManifest.model_validate(storage.load_json_file(content_id, "manifest.json", source_prefix))
    except Exception:
        return

    base_url = _public_url_for_prefix(storage, target_prefix).rstrip("/")
    snapshot_files: list[GeneratedFile] = []
    for item in manifest.items:
        pack_name = _url_basename(item.url)
        if not pack_name or pack_name == updated_pack_name:
            item.url = f"{base_url}/{pack_name or updated_pack_name}"
            continue
        try:
            content = storage.load_json_file(content_id, pack_name, source_prefix)
        except Exception:
            item.url = f"{base_url}/{pack_name}"
            continue
        if content.get("type") in {"document", "quiz"}:
            content["assetBaseUrl"] = base_url
            snapshot_files.append(GeneratedFile(name=pack_name, kind=content["type"], content=content))
        item.url = f"{base_url}/{pack_name}"

    if snapshot_files:
        storage.save_files(content_id, snapshot_files, target_prefix)

    manifest.versionId = version_id
    manifest.buildId = build_id
    manifest.generatedAt = generated_at
    manifest_file = GeneratedFile(
        name="manifest.json",
        kind="manifest",
        content=manifest.model_dump(exclude_none=True),
    )
    storage.save_files(content_id, [manifest_file], target_prefix)
