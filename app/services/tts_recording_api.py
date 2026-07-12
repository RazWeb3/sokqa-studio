"""API-facing helpers for synchronous TTS recording."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from app.config import get_settings
from app.schemas.pack_v2 import (
    AudioObject,
    ChangedPackFile,
    CommitPackRevisionInput,
    ManifestItemV2,
    PackManifestV2,
)
from app.schemas.request import TtsRecordingTarget
from app.schemas.sokqa import GeneratedFile, SokqaDocumentPack, SokqaQuizPack
from app.services.pack_metadata import pack_storage_prefix
from app.services.pack_paths import (
    audio_object_relative_path,
    doc_object_relative_path,
    generate_audio_version_id,
    pack_root_prefix,
    quiz_object_relative_path,
    resolve_asset_url,
    validate_safe_token,
)
from app.services.revision_store import persist_revision_commit
from app.services.storage_client import StorageClient
from app.services.tts_estimation import RecordingTextSource, RecordingUnit, extract_recording_units
from app.services.tts_language_tags import speech_segment_count
from app.services.tts_recorder import RecordingSummary, Synthesizer, clear_pack_audio_urls, record_generated_file_audio


def _chunk_units(units: list[RecordingUnit], chunk_size: int) -> list[list[RecordingUnit]]:
    return [units[index : index + chunk_size] for index in range(0, len(units), chunk_size)]


def _merge_recording_summaries(summaries: list[RecordingSummary]) -> RecordingSummary:
    results = [result for summary in summaries for result in summary.results]
    failed_unit_ids = [unit_id for summary in summaries for unit_id in summary.failed_unit_ids]
    return RecordingSummary(
        total_units=sum(summary.total_units for summary in summaries),
        skipped_units=sum(summary.skipped_units for summary in summaries),
        success_count=sum(summary.success_count for summary in summaries),
        failure_count=sum(summary.failure_count for summary in summaries),
        failed_unit_ids=failed_unit_ids,
        results=sorted(results, key=lambda result: result.unit_id),
    )


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

    loaded = load_target_pack(target)
    creator_id, content_id, _ = _identity_from_storage_prefix(loaded.storage_prefix)
    _validate_target_matches_loaded_prefix(target, creator_id, content_id, loaded.storage_prefix)
    storage = storage_client or StorageClient()
    all_units = extract_recording_units(loaded.pack, text_source)
    selected = _select_units(all_units, unit_ids, include_recorded=True)
    recording_targets = [unit for unit in selected if force_rerecord or not unit.is_recorded]
    pack_root = pack_root_prefix(creator_id, content_id)
    current_manifest = _load_current_manifest_v2(storage, loaded, creator_id, content_id)
    target_logical_id = _logical_id_from_pack_name(loaded.file.name)
    current_item = _manifest_item_for_target(current_manifest, loaded.file.name, target_logical_id)
    audio_path_by_unit: dict[str, str] = {}

    def audio_path_for_unit(unit: RecordingUnit) -> str:
        audio_version_id = generate_audio_version_id(f"{target_logical_id}__{unit.item_id}")
        audio_path = audio_object_relative_path(audio_version_id)
        audio_path_by_unit[unit.item_id] = audio_path
        return audio_path

    kwargs = {"storage_client": storage}
    if synthesize_fn is not None:
        kwargs["synthesize_fn"] = synthesize_fn
    summaries = [
        record_generated_file_audio(
            loaded.file,
            chunk,
            pack_root,
            force_rerecord=force_rerecord,
            language_code=language_code,
            voice_name=voice_name,
            speaking_rate=speaking_rate,
            pitch=pitch,
            persist_file=False,
            store_audio=False,
            audio_path_factory=audio_path_for_unit,
            **kwargs,
        )
        for chunk in _chunk_units(selected, max_units)
    ]
    summary = _merge_recording_summaries(summaries)
    loaded.pack = _pack_from_file(loaded.file)
    successful_results = [result for result in summary.results if result.success and result.audio_path and result.audio_data]
    commit_result = None
    if successful_results:
        commit_result = persist_revision_commit(
            storage,
            current_manifest,
            CommitPackRevisionInput(
                target={
                    "creatorId": creator_id,
                    "contentId": content_id,
                    "versionId": current_manifest.versionId,
                },
                operation="recording",
                changedFiles=[
                    ChangedPackFile(
                        name=loaded.file.name,
                        kind=loaded.file.kind,
                        logicalId=target_logical_id,
                        previousFileVersionId=current_item.fileVersionId,
                        content=loaded.file.content,
                    )
                ],
                newAudioObjects=[
                    AudioObject(
                        audioVersionId=_audio_version_id_from_path(result.audio_path),
                        relativePath=result.audio_path,
                        data=result.audio_data,
                    )
                    for result in successful_results
                ],
            ),
            public_base_url=get_settings().public_base_url,
        )
    response_version_id = commit_result.versionId if commit_result else current_manifest.versionId
    asset_base_url = commit_result.assetBaseUrl if commit_result else loaded.pack.assetBaseUrl or _public_url_for_prefix(storage, pack_root)
    if commit_result:
        pack_url = next(item.url for item in commit_result.items if item.logicalId == target_logical_id)
    else:
        pack_url = f"{asset_base_url.rstrip('/')}/{loaded.file.name}"
    return {
        "packId": loaded.pack.id,
        "packType": loaded.pack.type,
        "packName": loaded.file.name,
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": response_version_id,
        "buildId": commit_result.manifest.buildId if commit_result else None,
        "generatedAt": commit_result.manifest.generatedAt if commit_result else None,
        "storagePrefix": pack_root,
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
    creator_id, content_id, _ = _identity_from_storage_prefix(loaded.storage_prefix)
    _validate_target_matches_loaded_prefix(target, creator_id, content_id, loaded.storage_prefix)
    storage = StorageClient()
    current_manifest = _load_current_manifest_v2(storage, loaded, creator_id, content_id)
    target_logical_id = _logical_id_from_pack_name(loaded.file.name)
    current_item = _manifest_item_for_target(current_manifest, loaded.file.name, target_logical_id)
    all_units = extract_recording_units(loaded.pack, text_source)
    selected = _select_units(all_units, unit_ids, include_recorded=True)
    cleared_count = clear_pack_audio_urls(loaded.pack, selected)
    loaded.file.content = loaded.pack.model_dump(exclude_none=True)
    commit_result = None
    if cleared_count:
        commit_result = persist_revision_commit(
            storage,
            current_manifest,
            CommitPackRevisionInput(
                target={
                    "creatorId": creator_id,
                    "contentId": content_id,
                    "versionId": current_manifest.versionId,
                },
                operation="recording_reset",
                changedFiles=[
                    ChangedPackFile(
                        name=loaded.file.name,
                        kind=loaded.file.kind,
                        logicalId=target_logical_id,
                        previousFileVersionId=current_item.fileVersionId,
                        content=loaded.file.content,
                    )
                ],
            ),
            public_base_url=get_settings().public_base_url,
        )
    refreshed_units = extract_recording_units(loaded.pack, text_source)
    affected_ids = {unit.item_id for unit in selected}
    response_version_id = commit_result.versionId if commit_result else current_manifest.versionId
    pack_root = pack_root_prefix(creator_id, content_id)
    return {
        "packId": loaded.pack.id,
        "packType": loaded.pack.type,
        "packName": loaded.file.name,
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": response_version_id,
        "buildId": commit_result.manifest.buildId if commit_result else None,
        "generatedAt": commit_result.manifest.generatedAt if commit_result else None,
        "storagePrefix": pack_root if commit_result else loaded.storage_prefix,
        "assetBaseUrl": commit_result.assetBaseUrl if commit_result else loaded.pack.assetBaseUrl,
        "target": {
            "creatorId": creator_id,
            "contentId": content_id,
            "versionId": response_version_id,
            "packName": loaded.file.name,
            "kind": loaded.file.kind,
        },
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
    if target.temporaryGenerationId:
        from app.services.temporary_generation_store import get_temporary_generation

        temporary = get_temporary_generation(target.temporaryGenerationId, str(target.creatorId))
        candidates = [file for file in temporary.files if file.kind in {"document", "quiz"}]
        if target.packName:
            candidates = [file for file in candidates if file.name == target.packName]
        if target.kind:
            candidates = [file for file in candidates if file.kind == target.kind]
        if len(candidates) != 1:
            raise ValueError("temporary generation target is ambiguous; provide packName and kind")
        file = candidates[0]
        return _loaded_from_content(file.content, file.name, f"temporary/{temporary.id}")
    if target.packUrl:
        pack_name = target.packName or _url_basename(target.packUrl)
        if not (target.creatorId and target.contentId and target.versionId):
            raise ValueError("packUrl targets require creatorId/contentId/versionId in v2 mode")
        storage_prefix = pack_storage_prefix(target.creatorId, target.contentId, target.versionId)
        return _loaded_from_content(_load_json_url(target.packUrl), pack_name, storage_prefix)

    if target.manifestUrl:
        manifest = PackManifestV2.model_validate(_load_json_url(target.manifestUrl))
        item_url = _select_manifest_item_url(manifest, target)
        pack_name = target.packName or _url_basename(item_url)
        if not (target.creatorId and target.contentId and target.versionId):
            raise ValueError("manifestUrl targets require creatorId/contentId/versionId in v2 mode")
        storage_prefix = pack_storage_prefix(target.creatorId, target.contentId, target.versionId)
        return _loaded_from_content(_load_json_url(item_url), pack_name, storage_prefix)

    if target.creatorId and target.contentId and target.versionId and target.packName:
        storage_prefix = pack_storage_prefix(target.creatorId, target.contentId, target.versionId)
        storage = StorageClient()
        content = _load_v2_pack_content(storage, target)
        return _loaded_from_content(content, target.packName, storage_prefix)

    raise ValueError("target must include packUrl, manifestUrl, or creatorId/contentId/versionId/packName")


def _load_v2_pack_content(storage: StorageClient, target: TtsRecordingTarget) -> dict:
    if not (target.creatorId and target.contentId and target.versionId and target.packName):
        raise ValueError("v2 target requires creatorId/contentId/versionId/packName")
    prefix = pack_root_prefix(target.creatorId, target.contentId)
    manifest = PackManifestV2.model_validate(storage.read_manifest(prefix, target.versionId))
    candidates = [item for item in manifest.items if item.name == target.packName]
    if target.kind:
        candidates = [item for item in candidates if item.kind == target.kind]
    if len(candidates) != 1:
        raise ValueError("v2 manifest target is ambiguous; provide packName or kind")
    relative_path = _relative_path_from_v2_item(candidates[0], prefix)
    return json.loads(storage.read_object(prefix, relative_path).decode("utf-8"))


def _relative_path_from_v2_item(item: ManifestItemV2, prefix: str) -> str:
    path = urlparse(item.url).path.strip("/")
    if path.startswith("generated/"):
        path = path.removeprefix("generated/")
    marker = f"{prefix}/"
    if marker in path:
        relative = path.split(marker, 1)[1]
        if relative.startswith("objects/"):
            return relative
    if item.kind == "document":
        return doc_object_relative_path(item.fileVersionId)
    if item.kind == "quiz":
        return quiz_object_relative_path(item.fileVersionId)
    raise ValueError(f"unsupported v2 manifest item kind: {item.kind}")


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


def _select_manifest_item_url(manifest: PackManifestV2, target: TtsRecordingTarget) -> str:
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
    default_language_code = get_settings().cloud_tts_language_code
    synthesis_request_count = sum(speech_segment_count(unit.text, default_language_code) for unit in billable_units)
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
        "synthesisRequestCount": synthesis_request_count,
        "estimatedCredits": total_chars * rate,
        "units": [_unit_to_dict(unit) for unit in units],
    }


def _load_current_manifest_v2(
    storage: StorageClient,
    loaded: LoadedPack,
    creator_id: str,
    content_id: str,
) -> PackManifestV2:
    _, _, source_version_id = _identity_from_storage_prefix(loaded.storage_prefix)
    pack_root = pack_root_prefix(creator_id, content_id)
    manifest = PackManifestV2.model_validate(storage.read_manifest(pack_root, source_version_id))
    return manifest


def _manifest_item_for_target(manifest: PackManifestV2, pack_name: str, logical_id: str) -> ManifestItemV2:
    for item in manifest.items:
        if item.logicalId == logical_id:
            return item
    raise ValueError(f"manifest does not contain target pack file: {pack_name}")


def _logical_id_from_pack_name(pack_name: str) -> str:
    stem = _url_basename(pack_name)
    if stem.lower().endswith(".json"):
        stem = stem[:-5]
    return validate_safe_token(stem)


def _audio_version_id_from_path(audio_path: str) -> str:
    name = audio_path.rsplit("/", 1)[-1]
    if not name.endswith(".mp3"):
        raise ValueError("audioPath must end with .mp3")
    return validate_safe_token(name[:-4])


def _validate_target_matches_loaded_prefix(
    target: TtsRecordingTarget,
    creator_id: str,
    content_id: str,
    storage_prefix: str,
) -> None:
    _, _, version_id = _identity_from_storage_prefix(storage_prefix)
    if target.creatorId and target.creatorId != creator_id:
        raise ValueError("target creatorId does not match loaded pack")
    if target.contentId and target.contentId != content_id:
        raise ValueError("target contentId does not match loaded pack")
    if target.versionId and target.versionId != version_id:
        raise ValueError("target versionId does not match loaded pack")


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
