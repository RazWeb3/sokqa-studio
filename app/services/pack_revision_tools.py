import io
import copy
import json
import zipfile
from urllib.parse import urlparse
from urllib.request import urlopen

from app.schemas.common import TtsRule
from app.schemas.pack_v2 import ChangedPackFile, CommitPackRevisionInput, PackManifestV2, RevisionTarget
from app.schemas.request import RevisePackTtsRequest, TtsRecordingTarget
from app.schemas.sokqa import GeneratedFile, PackRevisionResponse, TtsReport
from app.services.pack_paths import doc_object_relative_path, pack_root_prefix, quiz_object_relative_path
from app.services.revision_commit import build_revision_commit
from app.services.revision_store import persist_revision_commit
from app.services.storage_client import StorageClient
from app.services.tts_optimizer import _apply_rule_replacements
from app.services.validator import validate_files


def revise_pack_tts(request: RevisePackTtsRequest) -> PackRevisionResponse:
    if not request.ttsRules:
        raise ValueError("ttsRules must not be empty")
    storage = StorageClient()
    manifest, creator_id, content_id = _resolve_manifest(storage, request.target)
    files = _files_from_manifest(storage, manifest, creator_id, content_id)
    revised_files = apply_tts_replacement_rules(files, request.ttsRules)
    changed_files = [
        ChangedPackFile(
            name=file.name,
            kind=file.kind,
            logicalId=_logical_id_from_name(file.name),
            previousFileVersionId=_manifest_item_by_name(manifest, file.name).fileVersionId,
            content=file.content,
        )
        for file in revised_files
    ]
    tts_report = TtsReport(mode="rule")
    if not changed_files:
        response_files = [*files, GeneratedFile(name="manifest.json", kind="manifest", content=manifest.model_dump(mode="json", exclude_none=True))]
        validation = validate_files(response_files, manifest)
        return PackRevisionResponse(
            status="completed",
            files=response_files,
            manifest=manifest,
            validation=validation,
            ttsReport=tts_report,
            logs=["Loading latest manifest", "Applying TTS replacement rules", "No TTS replacement changes"],
        )
    commit_request = CommitPackRevisionInput(
        target=RevisionTarget(creatorId=creator_id, contentId=content_id, versionId=manifest.versionId),
        operation="tts_fix",
        changedFiles=changed_files,
    )
    result = (
        persist_revision_commit(storage, manifest, commit_request)
        if request.persist
        else build_revision_commit(manifest, commit_request)
    )
    response_files = _files_from_revision_result(result)
    response_files.append(
        GeneratedFile(
            name="manifest.json",
            kind="manifest",
            content=result.manifest.model_dump(mode="json", exclude_none=True),
            url=result.manifestUrl,
        )
    )
    validation = validate_files(response_files, result.manifest)
    return PackRevisionResponse(
        status="completed",
        files=response_files,
        manifest=result.manifest,
        validation=validation,
        ttsReport=tts_report,
        logs=["Loading latest manifest", "Applying TTS replacement rules", "Persisting TTS revision"],
    )


def apply_tts_replacement_rules(files: list[GeneratedFile], rules: list[TtsRule]) -> list[GeneratedFile]:
    revised: list[GeneratedFile] = []
    for file in files:
        content = copy.deepcopy(file.content)
        if file.kind == "document":
            _patch_document_pack_tts(content, rules)
        elif file.kind == "quiz":
            _patch_quiz_pack_tts(content, rules)
        if content != file.content:
            revised.append(GeneratedFile(name=file.name, kind=file.kind, content=content, url=file.url))
    return revised


def _patch_document_pack_tts(content: dict, rules: list[TtsRule]) -> None:
    for item in content.get("documents") or []:
        source_text = item.get("text")
        if not isinstance(source_text, str):
            continue
        tts = item.get("tts")
        if isinstance(tts, dict) and isinstance(tts.get("text"), str) and tts.get("text"):
            changed = _patch_text_field(tts, "text", rules)
        else:
            reading = _replacement_or_none(source_text, rules)
            changed = reading is not None
            if changed:
                tts = tts if isinstance(tts, dict) else {}
                tts["text"] = reading
                item["tts"] = tts
        if changed and isinstance(tts, dict):
            tts.pop("audioUrl", None)
            tts.pop("audioPath", None)


def _patch_quiz_pack_tts(content: dict, rules: list[TtsRule]) -> None:
    for question in content.get("questions") or []:
        tts = question.get("tts")
        if not isinstance(tts, dict):
            tts = {}
        changed = False
        changed = _patch_or_create_quiz_text_field(question, tts, "question", "questionText", rules) or changed
        if _patch_quiz_choice_texts(question, tts, rules):
            changed = True
        changed = _patch_or_create_quiz_text_field(question, tts, "explanation", "explanationText", rules) or changed
        if changed:
            question["tts"] = tts
        elif not question.get("tts"):
            question.pop("tts", None)


def _patch_or_create_quiz_text_field(question: dict, tts: dict, source_key: str, tts_key: str, rules: list[TtsRule]) -> bool:
    existing = tts.get(tts_key)
    if isinstance(existing, str) and existing:
        changed = _patch_text_field(tts, tts_key, rules)
    else:
        source_text = question.get(source_key)
        reading = _replacement_or_none(source_text, rules) if isinstance(source_text, str) else None
        changed = reading is not None
        if changed:
            tts[tts_key] = reading
    if changed:
        if tts_key == "questionText":
            tts.pop("questionAudioUrl", None)
            tts.pop("questionAudioPath", None)
        elif tts_key == "explanationText":
            tts.pop("explanationAudioUrl", None)
            tts.pop("explanationAudioPath", None)
    return changed


def _patch_quiz_choice_texts(question: dict, tts: dict, rules: list[TtsRule]) -> bool:
    choices = question.get("choices")
    if not isinstance(choices, list):
        return False
    existing = tts.get("choiceTexts")
    choice_texts = list(existing) if isinstance(existing, list) else [""] * len(choices)
    if len(choice_texts) < len(choices):
        choice_texts.extend([""] * (len(choices) - len(choice_texts)))
    changed_indexes: list[int] = []
    for index, choice in enumerate(choices):
        if not isinstance(choice, str):
            continue
        current = choice_texts[index] if index < len(choice_texts) else ""
        if isinstance(current, str) and current:
            replacement = _replacement_or_none(current, rules)
        else:
            replacement = _replacement_or_none(choice, rules)
        if replacement is not None and replacement != current:
            choice_texts[index] = replacement
            changed_indexes.append(index)
    if not changed_indexes:
        return False
    tts["choiceTexts"] = choice_texts
    _clear_choice_audio(tts, changed_indexes, len(choices))
    return True


def _clear_choice_audio(tts: dict, changed_indexes: list[int], choice_count: int) -> None:
    for key in ["choiceAudioUrls", "choiceAudioPaths"]:
        values = tts.get(key)
        if not isinstance(values, list):
            continue
        values = list(values)
        if len(values) < choice_count:
            values.extend([None] * (choice_count - len(values)))
        for index in changed_indexes:
            if index < len(values):
                values[index] = None
        if any(values):
            tts[key] = values
        else:
            tts.pop(key, None)


def _patch_text_field(container: dict, key: str, rules: list[TtsRule]) -> bool:
    value = container.get(key)
    if not isinstance(value, str) or not value:
        return False
    replacement = _replacement_or_none(value, rules)
    if replacement is None:
        return False
    container[key] = replacement
    return True


def _replacement_or_none(value: str, rules: list[TtsRule]) -> str | None:
    replaced = _apply_rule_replacements(value, rules)
    return replaced if replaced != value else None


def export_pack_json_zip(target: TtsRecordingTarget) -> bytes:
    storage = StorageClient()
    manifest, creator_id, content_id = _resolve_manifest(storage, target)
    files = _files_from_manifest(storage, manifest, creator_id, content_id)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2),
        )
        for file in files:
            archive.writestr(file.name, json.dumps(file.content, ensure_ascii=False, indent=2))
    return buffer.getvalue()


def _resolve_manifest(storage: StorageClient, target: TtsRecordingTarget) -> tuple[PackManifestV2, str, str]:
    if target.manifestUrl:
        manifest = PackManifestV2.model_validate(_load_json_url(target.manifestUrl))
        creator_id = target.creatorId or (manifest.creator.id if manifest.creator else None)
        if not creator_id:
            raise ValueError("creatorId is required when manifest has no creator")
        return manifest, creator_id, target.contentId or manifest.contentId
    if target.creatorId and target.contentId:
        pack_root = pack_root_prefix(target.creatorId, target.contentId)
        if target.versionId:
            return PackManifestV2.model_validate(storage.read_manifest(pack_root, target.versionId)), target.creatorId, target.contentId
        latest = storage.read_latest(pack_root)
        if not latest:
            raise ValueError("latest manifest is not available for target")
        return PackManifestV2.model_validate(latest), target.creatorId, target.contentId
    raise ValueError("target must include manifestUrl or creatorId/contentId")


def _files_from_manifest(storage: StorageClient, manifest: PackManifestV2, creator_id: str, content_id: str) -> list[GeneratedFile]:
    pack_root = pack_root_prefix(creator_id, content_id)
    files: list[GeneratedFile] = []
    for item in manifest.items:
        relative_path = _relative_path_for_item(item.kind, item.fileVersionId)
        content = json.loads(storage.read_object(pack_root, relative_path).decode("utf-8"))
        files.append(GeneratedFile(name=item.name, kind=item.kind, content=content, url=item.url))
    return files


def _relative_path_for_item(kind: str, file_version_id: str) -> str:
    if kind == "document":
        return doc_object_relative_path(file_version_id)
    if kind == "quiz":
        return quiz_object_relative_path(file_version_id)
    raise ValueError(f"unsupported manifest item kind: {kind}")


def _logical_id_from_name(name: str) -> str:
    return name[:-5] if name.lower().endswith(".json") else name


def _manifest_item_by_name(manifest: PackManifestV2, name: str):
    for item in manifest.items:
        if item.name == name:
            return item
    raise ValueError(f"manifest item not found: {name}")


def _files_from_revision_result(result) -> list[GeneratedFile]:
    objects_by_name = {obj.name: obj for obj in [*result.docObjects, *result.quizObjects]}
    files: list[GeneratedFile] = []
    for item in result.items:
        obj = objects_by_name.get(item.name)
        content = obj.content if obj is not None else {}
        files.append(GeneratedFile(name=item.name, kind=item.kind, content=content, url=item.url))
    return files


def _load_json_url(url: str) -> dict:
    parsed = urlparse(url)
    if parsed.hostname in {"localhost", "127.0.0.1"} and parsed.path.startswith("/generated/"):
        from pathlib import Path

        from app.config import get_settings

        relative = parsed.path.removeprefix("/generated/").lstrip("/")
        path = Path.cwd() / get_settings().local_storage_dir / relative
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    with urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))
