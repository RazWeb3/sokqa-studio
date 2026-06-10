"""API-facing helpers for synchronous TTS recording."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from app.config import get_settings
from app.schemas.request import TtsRecordingTarget
from app.schemas.sokqa import GeneratedFile, PackManifest, SokqaDocumentPack, SokqaQuizPack
from app.services.pack_metadata import pack_storage_prefix
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
    all_units = extract_recording_units(loaded.pack, text_source)
    selected = _select_units(all_units, unit_ids, include_recorded=True)
    kwargs = {"storage_client": storage_client}
    if synthesize_fn is not None:
        kwargs["synthesize_fn"] = synthesize_fn
    summary = record_generated_file_audio(
        loaded.file,
        selected,
        loaded.storage_prefix,
        force_rerecord=force_rerecord,
        language_code=language_code,
        voice_name=voice_name,
        speaking_rate=speaking_rate,
        pitch=pitch,
        **kwargs,
    )
    loaded.pack = _pack_from_file(loaded.file)
    return {
        "packId": loaded.pack.id,
        "packType": loaded.pack.type,
        "packName": loaded.file.name,
        "storagePrefix": loaded.storage_prefix,
        "textSource": text_source,
        "forceRerecord": force_rerecord,
        "summary": _summary_to_dict(summary),
        "audioUrls": [
            {
                "unitId": result.unit_id,
                "audioUrl": result.audio_url,
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
