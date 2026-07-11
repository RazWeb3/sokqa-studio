from __future__ import annotations

import copy
import json
import logging
import re
import time
import unicodedata
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.schemas.quality import QualityIssue, QualityLocation
from app.schemas.quality_fix import (
    AppliedFix,
    PendingFix,
    QualityFixApplyResponse,
    QualityFixResponse,
    QualityFixSaveFile,
    QualityFixSaveResponse,
    ReRecordNeededUnit,
    UnappliedFix,
)
from app.schemas.pack_v2 import (
    ChangedPackFile,
    ChangedUnit,
    CommitPackRevisionInput,
    ReRecordNeededUnit as ManifestReRecordNeededUnit,
)
from app.schemas.sokqa import DocumentTts, GeneratedFile, QuizTts, SokqaDocumentPack, SokqaQuizPack
from app.schemas.request import TtsRecordingTarget
from app.services.gemini_client import GeminiClient
from app.services.language_detection import leading_script
from app.services.llm_json import LlmJsonParseContext
from app.services.multilingual_detection import MultilingualStatus, detect_multilingual
from app.services.pack_paths import pack_root_prefix
from app.services.generation.language_learning.quality import deterministic_tts_issues
from app.services.quality_checker import _generate_json_with_retry
from app.services.revision_store import persist_revision_commit
from app.services.storage_client import StorageClient
from app.services.tts_recording_api import (
    _identity_from_storage_prefix,
    _load_current_manifest_v2,
    _manifest_item_for_target,
    _relative_path_from_v2_item,
    _url_basename,
    _validate_target_matches_loaded_prefix,
    load_target_pack,
)
from app.services.tts_text import collapse_duplicate_katakana_utterances
from app.services.validator import validate_files


# These categories may be *examined* by the TTS fixer.  They are not, by
# themselves, permission to alter content: _is_safe_auto_tts_repair is the
# allow-list used before every automatic application.
AUTO_CATEGORIES = {"reading", "double_utterance", "notation", "tts_text_mismatch"}
PENDING_CATEGORIES = {"factual", "style", "leak"}
MAX_FIX_INPUT_CHARS = 30000
logger = logging.getLogger(__name__)
_TTS_TAG_RE = re.compile(r"\[(?:[a-z]{2,3}(?:-[A-Za-z0-9]+)*)\]|<[^>]+>")
# 多言語パックで許可されない閉じタグ [/xx-YY] の検出用。Sokqa に [/...] 閉じタグは存在しない。
_CLOSING_LANGUAGE_TAG_RE = re.compile(r"\[/[a-z]{2,3}(?:-[A-Za-z0-9]+)*\]")
_LANGUAGE_TAG_RE = re.compile(r"\[([a-z]{2,3}(?:-[A-Za-z0-9]+)*)\]")
_SEMANTIC_REWRITE_LOANWORDS = {
    "アプリ",
    "ケーブル",
    "クラウド",
    "サービス",
    "サーバ",
    "サーバー",
    "システム",
    "ソフト",
    "ソフトウェア",
    "ツール",
    "データ",
    "ネットワーク",
}


class QualityFixError(RuntimeError):
    pass


def _llm_failure_message(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError) or "expecting value" in str(exc).lower():
        logger.warning("quality fix LLM returned empty or invalid JSON: %s", exc)
        return "LLM が有効な応答を返さなかったため、修正案を生成できませんでした。再試行してください。"
    logger.warning("quality fix LLM call failed: %s", exc)
    return f"quality fix LLM call failed: {exc}"


def generate_quality_fix(target: TtsRecordingTarget, issues: list[QualityIssue], max_fixes: int = 50) -> QualityFixResponse:
    return generate_tts_fix(target, issues, max_fixes)


def generate_tts_fix(target: TtsRecordingTarget, issues: list[QualityIssue], max_fixes: int = 50) -> QualityFixResponse:
    """Build a user-reviewable TTS fix preview.

    This endpoint never persists a revision.  The stricter auto-application
    allow-list is enforced by apply_auto_quality_fixes, which runs unattended
    during generation.  A caller must still explicitly save this preview.
    """
    loaded = load_target_pack(target)
    allow_language_tags = _allow_multilingual_tts_tags(loaded.file.content)
    return _generate_tts_fix_without_llm(
        target, [issue for issue in issues if issue.category in AUTO_CATEGORIES], max_fixes, allow_language_tags=allow_language_tags
    )


def generate_tts_fix_with_llm(target: TtsRecordingTarget, issues: list[QualityIssue], max_fixes: int = 50) -> QualityFixResponse:
    return _generate_quality_fix(target, [issue for issue in issues if issue.category in AUTO_CATEGORIES], max_fixes, mode="tts")


def generate_text_fix(target: TtsRecordingTarget, issues: list[QualityIssue], max_fixes: int = 50) -> QualityFixResponse:
    return _generate_quality_fix(target, [issue for issue in issues if issue.category in PENDING_CATEGORIES], max_fixes, mode="text")


def _generate_quality_fix(target: TtsRecordingTarget, issues: list[QualityIssue], max_fixes: int, *, mode: str) -> QualityFixResponse:
    loaded = load_target_pack(target)
    if mode == "tts":
        _validate_tts_fix_input(loaded.file.content)
        issues = [_normalize_tts_issue_location(issue) for issue in issues]
    allow_language_tags = _allow_multilingual_tts_tags(loaded.file.content) if mode == "tts" else False
    settings = get_settings()
    model = settings.fix_model
    limited_issues = issues[:max_fixes]

    if settings.gemini_provider == "mock":
        return _mock_fix_response(loaded.file.name, loaded.file.content, limited_issues, model, len(issues) > max_fixes)

    prompt, input_truncated = _fix_prompt(loaded.file.name, loaded.file.content, limited_issues, max_fixes, mode=mode)
    llm_start = time.perf_counter()
    llm_attempts = {"count": 0}

    fix_unit = "fix_text" if mode == "text" else "fix_tts"
    def _call_gemini() -> dict[str, Any]:
        llm_attempts["count"] += 1
        return GeminiClient().generate_json(
            prompt,
            model=model,
            temperature=0.2,
            parse_context=LlmJsonParseContext(
                generation_unit=fix_unit,
                model=model,
                title=loaded.file.name,
            ),
        )

    try:
        data = _generate_json_with_retry(_call_gemini)
    except Exception as exc:
        raise QualityFixError(_llm_failure_message(exc)) from exc
    finally:
        if mode == "tts":
            logger.info(
                "tts_fix_llm.llm_attempt_count=%s tts_fix_llm.llm_elapsed_ms=%s",
                llm_attempts["count"],
                int((time.perf_counter() - llm_start) * 1000),
            )

    try:
        response = _fix_response_from_data(
            data,
            file_name=loaded.file.name,
            original_json=loaded.file.content,
            model=model,
            max_fixes=max_fixes,
            input_truncated=input_truncated or len(issues) > max_fixes,
            allow_language_tags=allow_language_tags,
        )
        _validate_pack_json(loaded.file.name, response.updatedJson)
        return response
    except (TypeError, ValidationError, ValueError) as exc:
        raise QualityFixError(f"quality fix response validation failed: {exc}") from exc


def _generate_tts_fix_without_llm(
    target: TtsRecordingTarget,
    issues: list[QualityIssue],
    max_fixes: int,
    *,
    allow_language_tags: bool = False,
) -> QualityFixResponse:
    started = time.perf_counter()
    loaded = load_target_pack(target)
    _validate_tts_fix_input(loaded.file.content)
    settings = get_settings()
    model = settings.fix_model
    limited_issues = issues[:max_fixes]
    updated_json = copy.deepcopy(loaded.file.content)
    applied: list[AppliedFix] = []
    unapplied: list[UnappliedFix] = []
    skipped = 0
    input_chars = len(json.dumps(loaded.file.content, ensure_ascii=False))

    for index, issue in enumerate(limited_issues, start=1):
        issue = _normalize_tts_issue_location(issue)
        location = issue.location
        if location.fileName != loaded.file.name:
            skipped += 1
            logger.info("tts fix skipped issue: file mismatch %s != %s", location.fileName, loaded.file.name)
            continue
        if not _find_unit(loaded.file.content, location.unitId):
            skipped += 1
            logger.info("tts fix skipped issue: unresolved location %s", location.model_dump())
            continue

        location = _resolve_tts_location_for_issue(updated_json, location, issue.excerpt)
        before = _get_tts_field(updated_json, location) or _get_raw_field(updated_json, location)
        if issue.category == "double_utterance":
            excerpt_text = _non_empty_text(issue.excerpt)
            if excerpt_text is None:
                unapplied.append(
                    _unapplied_fix(
                        issue,
                        updated_json,
                        index,
                        reason="excerpt が空のため、破壊的な全体置換を避けて未適用にしました。",
                    )
                )
                continue
            collapsed_excerpt = collapse_duplicate_katakana_utterances(excerpt_text)
            if collapsed_excerpt == excerpt_text:
                unapplied.append(
                    _unapplied_fix(
                        issue,
                        updated_json,
                        index,
                        reason="excerpt を畳み込み対象として解釈できなかったため未適用にしました。",
                    )
                )
                continue
            after = _apply_partial_tts_replacement(before, excerpt_text, collapsed_excerpt)
            if after is None:
                unapplied.append(
                    _unapplied_fix(
                        issue,
                        updated_json,
                        index,
                        reason="正規化後も excerpt が対象テキスト内に見つからないため、破壊的な全体上書きを避けて未適用にしました。",
                    )
                )
                continue
            if not _set_tts_field(updated_json, location, after):
                skipped += 1
                logger.info("tts fix skipped issue: failed to set tts field %s", location.model_dump())
                continue
            logger.info(
                "quality_fix.auto_applied file=%s unit_id=%s field=%s category=%s before=%r after=%r",
                loaded.file.name,
                location.unitId,
                _tts_field_name(updated_json, location),
                issue.category,
                before,
                after,
            )
            applied.append(
                AppliedFix(
                    id=f"auto-{index}",
                    category=issue.category,
                    location=location,
                    field=_tts_field_name(updated_json, location),
                    before=before,
                    after=after,
                    sourceIssue=issue.issue,
                )
            )
            continue
        replacement = _fix_after_text(issue.suggestion, location)
        if replacement is None or not _is_applicable_tts_suggestion(issue.suggestion, replacement):
            unapplied.append(
                _unapplied_fix(
                    issue,
                    updated_json,
                    index,
                    reason="suggestion が空、または適用可能な修正後テキストではありません。",
                )
            )
            continue
        after = _apply_partial_tts_replacement(before, issue.excerpt, replacement)
        if after is None:
            unapplied.append(
                _unapplied_fix(
                    issue,
                    updated_json,
                    index,
                    reason="正規化後も excerpt が対象テキスト内に見つからないため、破壊的な全体上書きを避けて未適用にしました。",
                )
            )
            continue
        if _is_clear_vocabulary_rewrite(issue.excerpt, replacement):
            unapplied.append(
                _unapplied_fix(
                    issue,
                    updated_json,
                    index,
                    reason="読み補正ではなく語彙変更の可能性があるため未適用にしました。",
                )
            )
            continue
        if _CLOSING_LANGUAGE_TAG_RE.search(after):
            unapplied.append(
                _unapplied_fix(
                    issue,
                    updated_json,
                    index,
                    reason="Sokqa のTTSタグは切替タグ方式のため、閉じタグは使用できません。",
                )
            )
            continue
        if allow_language_tags and _is_invalid_multilingual_tts_fix(issue.excerpt, replacement):
            unapplied.append(
                _unapplied_fix(
                    issue,
                    updated_json,
                    index,
                    reason="多言語パックで、閉じタグ付与や学習言語スパンのカタカナ化は許可されないため未適用にしました。",
                )
            )
            continue

        if not _set_tts_field(updated_json, location, after):
            skipped += 1
            logger.info("tts fix skipped issue: failed to set tts field %s", location.model_dump())
            continue

        logger.info(
            "quality_fix.auto_applied file=%s unit_id=%s field=%s category=%s before=%r after=%r",
            loaded.file.name,
            location.unitId,
            _tts_field_name(updated_json, location),
            issue.category,
            before,
            after,
        )
        applied.append(
            AppliedFix(
                id=f"auto-{index}",
                category=issue.category,
                location=location,
                field=_tts_field_name(updated_json, location),
                before=before,
                after=after,
                sourceIssue=issue.issue,
            )
        )

    try:
        _validate_pack_json(loaded.file.name, updated_json)
    except (TypeError, ValidationError, ValueError) as exc:
        raise QualityFixError(f"quality fix response validation failed: {exc}") from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "tts_fix.input_chars=%s tts_fix.issue_count=%s tts_fix.applied_count=%s "
        "tts_fix.unapplied_count=%s tts_fix.skipped_count=%s tts_fix.elapsed_ms=%s",
        input_chars,
        len(limited_issues),
        len(applied),
        len(unapplied),
        skipped,
        elapsed_ms,
    )
    return QualityFixResponse(
        fileName=loaded.file.name,
        model=f"{model}:no-llm",
        appliedFixes=applied,
        pendingFixes=[],
        unappliedFixes=unapplied,
        updatedJson=updated_json,
        reRecordNeededUnits=[
            ReRecordNeededUnit(
                fileName=fix.location.fileName,
                unitId=fix.location.unitId,
                field=fix.location.field,
                category=fix.category,
            )
            for fix in applied
        ],
        truncated=len(issues) > max_fixes,
    )


def apply_auto_quality_fixes(content: dict[str, Any], file_name: str) -> tuple[dict[str, Any], list[AppliedFix]]:
    """生成パイプライン用: content を直接受け取り、検出されたTTS問題のうち、
    表示本文との一致とタグ構造を決定論的に証明できるものだけを自動適用する。

    ストレージ未永続化の content でも動作するよう、load_target_pack を経由せず
    内部ロジックを直接呼び出す。PENDINGカテゴリ（factual/style/leak）は対象外。
    """
    updated_json = copy.deepcopy(content)
    issues = deterministic_tts_issues(file_name, updated_json)
    auto_issues = [issue for issue in issues if issue.category in AUTO_CATEGORIES]
    if not auto_issues:
        return updated_json, []
    applied = _apply_auto_issues_without_llm(updated_json, auto_issues, file_name)
    return updated_json, applied


def _apply_auto_issues_without_llm(
    updated_json: dict[str, Any],
    issues: list[QualityIssue],
    file_name: str,
) -> list[AppliedFix]:
    """_generate_tts_fix_without_llm の content 直接版（ストレージアクセスなし）。"""
    applied: list[AppliedFix] = []
    for index, issue in enumerate(issues, start=1):
        issue = _normalize_tts_issue_location(issue)
        location = issue.location
        if location.fileName != file_name:
            continue
        if not _find_unit(updated_json, location.unitId):
            continue
        location = _resolve_tts_location_for_issue(updated_json, location, issue.excerpt)
        before = _get_tts_field(updated_json, location) or _get_raw_field(updated_json, location)
        if issue.category == "double_utterance":
            excerpt_text = _non_empty_text(issue.excerpt)
            if excerpt_text is None:
                continue
            collapsed_excerpt = collapse_duplicate_katakana_utterances(excerpt_text)
            if collapsed_excerpt == excerpt_text:
                continue
            after = _apply_partial_tts_replacement(before, excerpt_text, collapsed_excerpt)
            if after is None:
                continue
        else:
            replacement = _fix_after_text(issue.suggestion, location)
            if replacement is None or not _is_applicable_tts_suggestion(issue.suggestion, replacement):
                continue
            after = _apply_partial_tts_replacement(before, issue.excerpt, replacement)
            if after is None:
                continue
            if _is_clear_vocabulary_rewrite(issue.excerpt, replacement):
                continue
            allow_language_tags = _allow_multilingual_tts_tags(updated_json)
            if allow_language_tags and _is_invalid_multilingual_tts_fix(issue.excerpt, replacement):
                continue
        if not _is_safe_auto_tts_repair(updated_json, location, before, after):
            continue
        if not _set_tts_field(updated_json, location, after):
            continue
        applied.append(
            AppliedFix(
                id=f"auto-{index}",
                category=issue.category,
                location=location,
                field=_tts_field_name(updated_json, location),
                before=before,
                after=after,
                sourceIssue=issue.issue,
            )
        )
    return applied


def apply_approved_fixes(
    updated_json: dict[str, Any],
    pending_fixes: list[PendingFix],
    approved_ids: list[str],
    *,
    reset_tts_on_text_change: bool = False,
) -> QualityFixApplyResponse:
    final_json = copy.deepcopy(updated_json)
    approved = set(approved_ids)
    applied: list[str] = []
    skipped: list[str] = []
    for fix in pending_fixes:
        if fix.id not in approved:
            skipped.append(fix.id)
            continue
        before = _get_raw_field(final_json, fix.location)
        if _set_raw_field(final_json, fix.location, fix.suggestedAfter):
            logger.info(
                "quality_fix.pending_approved file=%s unit_id=%s field=%s category=%s before=%r after=%r",
                fix.location.fileName,
                fix.location.unitId,
                fix.location.field or fix.field,
                fix.category,
                before,
                fix.suggestedAfter,
            )
            if reset_tts_on_text_change:
                _reset_unit_tts(final_json, fix.location)
            applied.append(fix.id)
        else:
            skipped.append(fix.id)
    _validate_pack_json("finalJson", final_json)
    return QualityFixApplyResponse(finalJson=final_json, appliedApprovedIds=applied, skippedIds=skipped)


def save_quality_fix_version(
    target: TtsRecordingTarget,
    files: list[QualityFixSaveFile],
    applied_fixes: list[AppliedFix],
    *,
    storage_client: StorageClient | None = None,
) -> QualityFixSaveResponse:
    loaded = load_target_pack(target)
    creator_id, content_id, _ = _identity_from_storage_prefix(loaded.storage_prefix)
    storage = storage_client or StorageClient()
    _validate_target_matches_loaded_prefix(target, creator_id, content_id, loaded.storage_prefix)
    current_manifest = _load_current_manifest_v2(storage, loaded, creator_id, content_id)

    text_changed = False
    response_files: list[QualityFixSaveFile] = []
    changed_files: list[ChangedPackFile] = []
    changed_units: list[ChangedUnit] = []
    manifest_rerecord_units: list[ManifestReRecordNeededUnit] = []
    text_changed_unit_keys: set[tuple[str, str | None]] = set()
    for file in files:
        content = copy.deepcopy(file.content)
        if content.get("type") != file.kind:
            raise ValueError(f"{file.name} kind does not match content type")
        original = _load_original_file_content(storage, current_manifest, loaded.storage_prefix, creator_id, content_id, file)
        raw_changed_units = _raw_text_changed_units(file.name, original, content)
        if raw_changed_units:
            text_changed = True
            for unit in raw_changed_units:
                text_changed_unit_keys.add((file.name, unit.unitId))
                _reset_unit_tts(content, QualityLocation(fileName=file.name, unitId=unit.unitId, field=unit.fields[0] if unit.fields else None))
                manifest_rerecord_units.append(
                    ManifestReRecordNeededUnit(fileName=file.name, unitId=unit.unitId, reason="text_changed")
                )
            changed_units.extend(raw_changed_units)
        for fix in applied_fixes:
            if fix.location.fileName != file.name:
                continue
            if (file.name, fix.location.unitId) in text_changed_unit_keys:
                continue
            _clear_audio_for_tts_fix(content, fix.location)
            manifest_rerecord_units.append(
                ManifestReRecordNeededUnit(fileName=file.name, unitId=fix.location.unitId, reason="tts_changed")
            )
            changed_units.append(
                ChangedUnit(
                    fileName=file.name,
                    unitId=fix.location.unitId,
                    fields=[fix.field],
                    category=fix.category,
                )
            )
        content = _validate_pack_json(file.name, content)
        logical_id = _logical_id_from_file_name(file.name)
        current_item = _manifest_item_for_target(current_manifest, file.name, logical_id)
        changed_files.append(
            ChangedPackFile(
                name=file.name,
                kind=file.kind,
                logicalId=logical_id,
                previousFileVersionId=current_item.fileVersionId,
                content=content,
            )
        )
        response_files.append(QualityFixSaveFile(name=file.name, kind=file.kind, content=content))

    operation = "text_fix" if text_changed else "tts_fix"
    commit_result = persist_revision_commit(
        storage,
        current_manifest,
        CommitPackRevisionInput(
            target={
                "creatorId": creator_id,
                "contentId": content_id,
                "versionId": current_manifest.versionId,
            },
            operation=operation,
            changedFiles=changed_files,
            changedUnits=changed_units,
            reRecordNeededUnits=_dedupe_manifest_rerecord_units(manifest_rerecord_units),
        ),
        public_base_url=get_settings().public_base_url,
    )
    saved_by_name = {obj.name: obj.content for obj in [*commit_result.docObjects, *commit_result.quizObjects]}
    return QualityFixSaveResponse(
        newVersionId=commit_result.versionId,
        newAssetBaseUrl=commit_result.assetBaseUrl,
        storagePrefix=pack_root_prefix(creator_id, content_id),
        files=[
            QualityFixSaveFile(name=file.name, kind=file.kind, content=saved_by_name.get(file.name, file.content))
            for file in response_files
        ],
        reRecordNeededUnits=[
            ReRecordNeededUnit(
                fileName=fix.location.fileName,
                unitId=fix.location.unitId,
                field=fix.location.field,
                category=fix.category,
            )
            for fix in applied_fixes
        ],
    )


def _validate_tts_fix_input(content: dict[str, Any]) -> None:
    if content.get("type") != "quiz":
        return
    for question_index, question in enumerate(content.get("questions") or []):
        if not isinstance(question, dict):
            continue
        tts = question.get("tts") or {}
        if not isinstance(tts, dict) or "choiceTexts" not in tts:
            continue
        choices = question.get("choices") or []
        choice_texts = tts.get("choiceTexts")
        path = f"questions.{question_index}.tts.choiceTexts"
        if choice_texts is None:
            continue
        if not isinstance(choice_texts, list):
            raise ValueError(f"{path} must be a list when present")
        if len(choice_texts) != len(choices):
            raise ValueError(f"{path} length must match choices length")
        for index, value in enumerate(choice_texts):
            if value is None:
                continue
        if not isinstance(value, str):
            raise ValueError(f"{path}.{index} must be a string or null")


def _load_original_file_content(
    storage: StorageClient,
    current_manifest,
    storage_prefix: str,
    creator_id: str,
    content_id: str,
    file: QualityFixSaveFile,
) -> dict[str, Any]:
    logical_id = _logical_id_from_file_name(file.name)
    item = _manifest_item_for_target(current_manifest, file.name, logical_id)
    prefix = pack_root_prefix(creator_id, content_id)
    relative_path = _relative_path_from_v2_item(item, prefix)
    return json.loads(storage.read_object(prefix, relative_path).decode("utf-8"))


def _logical_id_from_file_name(file_name: str) -> str:
    stem = _url_basename(file_name)
    if stem.lower().endswith(".json"):
        stem = stem[:-5]
    return stem


def _raw_text_changed_units(file_name: str, original: dict[str, Any], updated: dict[str, Any]) -> list[ChangedUnit]:
    if original.get("type") == "document":
        original_by_id = {item.get("id"): item for item in original.get("documents") or [] if isinstance(item, dict)}
        changed: list[ChangedUnit] = []
        for item in updated.get("documents") or []:
            if not isinstance(item, dict):
                continue
            before = original_by_id.get(item.get("id")) or {}
            if item.get("text") != before.get("text"):
                changed.append(ChangedUnit(fileName=file_name, unitId=item.get("id"), fields=["text"], category="text_fix"))
        return changed

    if original.get("type") == "quiz":
        original_by_id = {item.get("id"): item for item in original.get("questions") or [] if isinstance(item, dict)}
        changed = []
        for item in updated.get("questions") or []:
            if not isinstance(item, dict):
                continue
            before = original_by_id.get(item.get("id")) or {}
            fields: list[str] = []
            for field in ("question", "explanation"):
                if item.get(field) != before.get(field):
                    fields.append(field)
            if item.get("choices") != before.get("choices"):
                fields.append("choices")
            if fields:
                changed.append(ChangedUnit(fileName=file_name, unitId=item.get("id"), fields=fields, category="text_fix"))
        return changed
    return []


def _clear_audio_for_tts_fix(content: dict[str, Any], location: QualityLocation) -> None:
    unit = _find_unit(content, location.unitId)
    if not unit or not isinstance(unit.get("tts"), dict):
        return
    tts = dict(unit.get("tts") or {})
    if content.get("type") == "document":
        tts.pop("audioUrl", None)
        tts.pop("audioPath", None)
        unit["tts"] = DocumentTts.model_validate(tts).model_dump(exclude_none=True)
        return

    field = location.field or "question"
    if _is_choice_field(field):
        index = _choice_index(field)
        for key in ("choiceAudioUrls", "choiceAudioPaths"):
            values = list(tts.get(key) or [])
            if 0 <= index < len(values):
                values[index] = None
                if any(values):
                    tts[key] = values
                else:
                    tts.pop(key, None)
    elif field == "explanation":
        tts.pop("explanationAudioUrl", None)
        tts.pop("explanationAudioPath", None)
    else:
        tts.pop("questionAudioUrl", None)
        tts.pop("questionAudioPath", None)
    unit["tts"] = QuizTts.model_validate(tts).model_dump(exclude_none=True)


def _dedupe_manifest_rerecord_units(units: list[ManifestReRecordNeededUnit]) -> list[ManifestReRecordNeededUnit]:
    deduped: list[ManifestReRecordNeededUnit] = []
    seen: set[tuple[str, str | None, str]] = set()
    for unit in units:
        key = (unit.fileName, unit.unitId, unit.reason)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(unit)
    return deduped


def _fix_response_from_data(
    data: Any,
    *,
    file_name: str,
    original_json: dict[str, Any],
    model: str,
    max_fixes: int,
    input_truncated: bool,
    allow_language_tags: bool = False,
) -> QualityFixResponse:
    data = data if isinstance(data, dict) else {}
    raw_applied = data.get("appliedFixes", [])
    raw_pending = data.get("pendingFixes", [])
    updated_json = data.get("updatedJson")
    if not isinstance(raw_applied, list) or not isinstance(raw_pending, list) or not isinstance(updated_json, dict):
        raise ValueError("response must contain appliedFixes, pendingFixes, and updatedJson")
    updated_json = copy.deepcopy(original_json)
    applied = [
        fix
        for fix in (_normalize_applied_fix(item, original_json, updated_json, file_name, index, allow_language_tags=allow_language_tags) for index, item in enumerate(raw_applied, start=1))
        if fix is not None
    ]
    pending = [
        fix
        for fix in (_normalize_pending_fix(item, original_json, file_name, index) for index, item in enumerate(raw_pending, start=1))
        if fix is not None
    ]
    truncated = bool(data.get("truncated")) or input_truncated or len(applied) + len(pending) > max_fixes
    return QualityFixResponse(
        fileName=str(data.get("fileName") or file_name),
        model=model,
        appliedFixes=applied[:max_fixes],
        pendingFixes=pending[:max_fixes],
        reRecordNeededUnits=[
            ReRecordNeededUnit(
                fileName=fix.location.fileName,
                unitId=fix.location.unitId,
                field=fix.location.field,
                category=fix.category,
            )
            for fix in applied[:max_fixes]
        ],
        updatedJson=updated_json,
        truncated=truncated,
    )


def _normalize_applied_fix(
    raw: Any,
    original_json: dict[str, Any],
    updated_json: dict[str, Any],
    file_name: str,
    index: int,
    *,
    allow_language_tags: bool = False,
) -> AppliedFix | None:
    if not isinstance(raw, dict):
        logger.info("quality fix skipped applied fix: item is not an object")
        return None
    category = raw.get("category")
    if category not in AUTO_CATEGORIES:
        logger.info("quality fix skipped applied fix: unsupported category %r", category)
        return None
    location = _location_from_raw(raw.get("location"), file_name)
    if location is None or not _find_unit(original_json, location.unitId):
        logger.info("quality fix skipped applied fix: unresolved location %r", raw.get("location"))
        return None
    after = _fix_after_text(raw.get("after"), location)
    if after is None:
        logger.info("quality fix skipped applied fix: missing after for %s", raw.get("id"))
        return None
    before = _get_tts_field(original_json, location) or _get_raw_field(original_json, location)
    if _CLOSING_LANGUAGE_TAG_RE.search(after):
        logger.info("quality fix skipped applied fix: closing language tag is unsupported for %s", raw.get("id"))
        return None
    if allow_language_tags and _is_invalid_multilingual_tts_fix(_fix_after_text(raw.get("before"), location), after):
        logger.info("quality fix skipped applied fix: invalid multilingual tts fix (closing tag or latin-span katakana) for %s", raw.get("id"))
        return None
    _set_tts_field(updated_json, location, after)
    logger.info(
        "quality_fix.auto_applied file=%s unit_id=%s field=%s category=%s before=%r after=%r",
        file_name,
        location.unitId,
        _tts_field_name(original_json, location),
        category,
        before,
        after,
    )
    return AppliedFix(
        id=str(raw.get("id") or f"auto-{index}"),
        category=category,
        location=location,
        field=_tts_field_name(original_json, location),
        before=before,
        after=after,
        sourceIssue=str(raw.get("sourceIssue") or raw.get("issue") or "quality issue"),
    )


def _unapplied_fix(issue: QualityIssue, original_json: dict[str, Any], index: int, *, reason: str) -> UnappliedFix:
    return UnappliedFix(
        id=f"unapplied-{index}",
        category=issue.category,
        location=issue.location,
        field=_tts_field_name(original_json, issue.location),
        before=_get_tts_field(original_json, issue.location) or _get_raw_field(original_json, issue.location),
        suggestion=issue.suggestion,
        reason=reason,
        sourceIssue=issue.issue,
    )


def _normalize_pending_fix(raw: Any, original_json: dict[str, Any], file_name: str, index: int) -> PendingFix | None:
    if not isinstance(raw, dict):
        logger.info("quality fix skipped pending fix: item is not an object")
        return None
    category = raw.get("category")
    if category not in PENDING_CATEGORIES:
        logger.info("quality fix skipped pending fix: unsupported category %r", category)
        return None
    location = _location_from_raw(raw.get("location"), file_name)
    if location is None or not _find_unit(original_json, location.unitId):
        logger.info("quality fix skipped pending fix: unresolved location %r", raw.get("location"))
        return None
    suggested_after = _non_empty_text(raw.get("suggestedAfter"))
    if suggested_after is None:
        logger.info("quality fix skipped pending fix: missing suggestedAfter for %s", raw.get("id"))
        return None
    before = _get_raw_field(original_json, location)
    logger.info(
        "quality_fix.pending_applied file=%s unit_id=%s field=%s category=%s before=%r after=%r",
        file_name,
        location.unitId,
        location.field,
        category,
        before,
        suggested_after,
    )
    return PendingFix(
        id=str(raw.get("id") or f"pending-{index}"),
        category=category,
        location=location,
        field=str(raw.get("field") or location.field or "text"),
        before=before,
        suggestedAfter=suggested_after,
        reason=str(raw.get("reason") or "本文変更のため承認が必要です。"),
        sourceIssue=str(raw.get("sourceIssue") or raw.get("issue") or "quality issue"),
    )


def _location_from_raw(raw: Any, file_name: str) -> QualityLocation | None:
    if not isinstance(raw, dict):
        return None
    data = dict(raw)
    data["fileName"] = str(data.get("fileName") or file_name)
    try:
        return QualityLocation.model_validate(data)
    except ValidationError:
        return None


def _non_empty_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() else None


def _fix_after_text(value: Any, location: QualityLocation) -> str | None:
    if _is_choice_field(location.field):
        index = _choice_index(location.field)
        if isinstance(value, list):
            if 0 <= index < len(value):
                return _non_empty_text(value[index])
            return None
        if isinstance(value, dict):
            if "text" in value:
                return _non_empty_text(value.get("text"))
            if "choiceTexts" in value:
                return _fix_after_text(value.get("choiceTexts"), location)
        text = _non_empty_text(value)
        parsed = _parse_string_list(text)
        if parsed is not None:
            if 0 <= index < len(parsed):
                return _non_empty_text(parsed[index])
            return None
        return text
    return _non_empty_text(value)


def _apply_partial_tts_replacement(base: str | None, excerpt: str | None, replacement: str) -> str | None:
    base_text = _non_empty_text(base)
    excerpt_text = _non_empty_text(excerpt)
    if base_text is None or excerpt_text is None:
        return None
    if excerpt_text not in base_text:
        span = _find_normalized_tts_excerpt_span(base_text, excerpt_text)
        if span is None:
            return None
        start, end = span
        return f"{base_text[:start]}{replacement}{base_text[end:]}"
    return base_text.replace(excerpt_text, replacement, 1)


def _collapse_double_utterance(base: str | None) -> str | None:
    base_text = _non_empty_text(base)
    if base_text is None:
        return None
    collapsed = collapse_duplicate_katakana_utterances(base_text)
    return collapsed if collapsed != base_text else None


def _resolve_tts_location_for_issue(content: dict[str, Any], location: QualityLocation, excerpt: str | None) -> QualityLocation:
    if content.get("type") != "quiz" or not _is_choice_field(location.field) or _choice_field_has_explicit_index(location.field):
        return location
    unit = _find_unit(content, location.unitId)
    if not unit:
        return location
    choices = unit.get("choices") or []
    limit = max(4, len(choices))
    matches: list[QualityLocation] = []
    for index in range(limit):
        candidate = QualityLocation(
            fileName=location.fileName,
            unitId=location.unitId,
            field=f"tts.choiceTexts[{index}]",
        )
        before = _get_tts_field(content, candidate) or _get_raw_field(content, candidate)
        if _find_tts_excerpt_span(before, excerpt) is not None:
            matches.append(candidate)
    if len(matches) > 1:
        logger.info("tts fix generic choice field matched multiple choices; using first %s", location.model_dump())
    return matches[0] if matches else location


def _find_tts_excerpt_span(base: str | None, excerpt: str | None) -> tuple[int, int] | None:
    base_text = _non_empty_text(base)
    excerpt_text = _non_empty_text(excerpt)
    if base_text is None or excerpt_text is None:
        return None
    exact_index = base_text.find(excerpt_text)
    if exact_index >= 0:
        return exact_index, exact_index + len(excerpt_text)
    return _find_normalized_tts_excerpt_span(base_text, excerpt_text)


def _find_normalized_tts_excerpt_span(base_text: str, excerpt_text: str) -> tuple[int, int] | None:
    normalized_base, spans = _normalize_tts_search_text(base_text, keep_spans=True)
    normalized_excerpt, _ = _normalize_tts_search_text(excerpt_text, keep_spans=False)
    if not normalized_base or not normalized_excerpt:
        return None
    normalized_index = normalized_base.find(normalized_excerpt)
    if normalized_index < 0:
        return None
    start = spans[normalized_index][0]
    end = spans[normalized_index + len(normalized_excerpt) - 1][1]
    return start, end


def _normalize_tts_search_text(value: str, *, keep_spans: bool) -> tuple[str, list[tuple[int, int]]]:
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(value):
        tag_match = _TTS_TAG_RE.match(value, index)
        if tag_match:
            index = tag_match.end()
            continue
        char = value[index]
        category = unicodedata.category(char)
        if char.isspace() or char == "\u3000" or category.startswith("P"):
            index += 1
            continue
        normalized = unicodedata.normalize("NFKC", char).casefold()
        for normalized_char in normalized:
            if normalized_char.isspace() or unicodedata.category(normalized_char).startswith("P"):
                continue
            chars.append(normalized_char)
            if keep_spans:
                spans.append((index, index + 1))
        index += 1
    return "".join(chars), spans


def _is_clear_vocabulary_rewrite(excerpt: str | None, replacement: str | None) -> bool:
    excerpt_text = _non_empty_text(excerpt)
    replacement_text = _non_empty_text(replacement)
    if excerpt_text is None or replacement_text is None:
        return False
    normalized_excerpt, _ = _normalize_tts_search_text(excerpt_text, keep_spans=False)
    normalized_replacement, _ = _normalize_tts_search_text(replacement_text, keep_spans=False)
    if not normalized_excerpt or not normalized_replacement or normalized_excerpt == normalized_replacement:
        return False
    if _adds_new_ideographs(excerpt_text, replacement_text):
        return True
    return _adds_semantic_loanword_to_mixed_term(excerpt_text, replacement_text)


def _allow_multilingual_tts_tags(content: dict[str, Any]) -> bool:
    """content が多言語パックなら True を返す。既存の multilingual 検出を利用。
    単一言語パックでは False(従来の挙動維持)。"""
    if not isinstance(content, dict):
        return False
    return detect_multilingual(content) == MultilingualStatus.MULTILINGUAL


def _is_invalid_multilingual_tts_fix(excerpt: str | None, replacement: str | None) -> bool:
    """多言語パックで許可しない TTS 修正を検出する。
    (a) 閉じタグ [/xx-YY] を含む置換(閉じタグの付与)。
    (b) 英字主体の excerpt をカタカナ主体へ変える置換(学習言語スパンのカタカナ化)。
    除去(単一言語)対象外の既存閉じタグの「削除」はこの関数では検出しない(許容)。"""
    excerpt_text = _non_empty_text(excerpt)
    replacement_text = _non_empty_text(replacement)
    if replacement_text is None:
        return False
    if _CLOSING_LANGUAGE_TAG_RE.search(replacement_text):
        return True
    if excerpt_text is not None and _is_latin_span_katakana_rewrite(excerpt_text, replacement_text):
        return True
    return False


def _is_safe_auto_tts_repair(
    content: dict[str, Any], location: QualityLocation, before: str | None, after: str | None
) -> bool:
    """Return True only for TTS-only edits that cannot change learner-facing text.

    The fixer must not trust an LLM category, confidence, or self-reported
    semantic impact.  It instead proves that the post-fix spoken text reduces
    to the same normalized display text and that any language switches use the
    supported, non-duplicated tag format.
    """
    before_text = _non_empty_text(before)
    after_text = _non_empty_text(after)
    source_text = _non_empty_text(_get_raw_field(content, location))
    if before_text is None or after_text is None or source_text is None or before_text == after_text:
        return False
    if _CLOSING_LANGUAGE_TAG_RE.search(after_text):
        return False
    if _normalize_tts_for_comparison(after_text) != _normalize_tts_for_comparison(source_text):
        return False
    return _has_valid_tts_tag_sequence(after_text)


def _normalize_tts_for_comparison(value: str) -> str:
    normalized, _ = _normalize_tts_search_text(value, keep_spans=False)
    return normalized


def _has_valid_tts_tag_sequence(value: str) -> bool:
    """Validate switch tags without attempting to infer semantics from an LLM."""
    tags = list(_LANGUAGE_TAG_RE.finditer(value))
    previous_end = 0
    previous_tag: str | None = None
    for match in tags:
        tag = match.group(1)
        # Sokqa uses BCP-47-like switch tags (for example en-US, ja-JP).
        if "-" not in tag:
            return False
        # A switch must govern at least one character.  Adjacent tags (whether
        # identical or not) are redundant and make the return boundary unclear.
        if previous_tag is not None and value[previous_end:match.start()] == "":
            return False
        previous_tag = tag
        previous_end = match.end()
    return True


def _is_latin_span_katakana_rewrite(excerpt: str, replacement: str) -> bool:
    """excerpt が学習対象言語(純ラテン文字列)で、replacement が主にカタカナの場合、学習言語
    スパンのカタカナ化とみなす。日本語文中の語(「SQLとJSON」等)を誤爆しないよう、excerpt に
    日本語文字(ひらがな/カタカナ/漢字)を含む場合は検出しない。"""
    if leading_script(excerpt) != "latin":
        return False
    if _contains_japanese_chars(excerpt):
        return False
    latin_ratio = _latin_char_ratio(excerpt)
    if latin_ratio < 0.6:
        return False
    katakana_ratio = _katakana_char_ratio(replacement)
    if katakana_ratio < 0.4:
        return False
    return True


def _contains_japanese_chars(value: str) -> bool:
    return any(0x3040 <= ord(char) <= 0x30FF or 0x4E00 <= ord(char) <= 0x9FFF for char in value)


def _latin_char_ratio(value: str) -> float:
    chars = [char for char in value if not char.isspace() and char not in "、。，．,.「」『』（）()[]【】〈〉《》"]
    if not chars:
        return 0.0
    latin = sum(1 for char in chars if char.isascii() and char.isalpha())
    return latin / len(chars)


def _katakana_char_ratio(value: str) -> float:
    chars = [char for char in value if not char.isspace() and char not in "、。，．,.「」『』（）()[]【】〈〉《》"]
    if not chars:
        return 0.0
    katakana = sum(1 for char in chars if 0x30A0 <= ord(char) <= 0x30FF or 0x31F0 <= ord(char) <= 0x31FF)
    return katakana / len(chars)


def _adds_new_ideographs(excerpt: str, replacement: str) -> bool:
    source = {char for char in excerpt if _is_cjk_ideograph(char)}
    added = {char for char in replacement if _is_cjk_ideograph(char) and char not in source}
    return bool(added)


def _adds_semantic_loanword_to_mixed_term(excerpt: str, replacement: str) -> bool:
    if not any(_is_cjk_ideograph(char) for char in excerpt):
        return False
    excerpt_normalized = unicodedata.normalize("NFKC", excerpt).casefold()
    replacement_normalized = unicodedata.normalize("NFKC", replacement).casefold()
    added_terms = [
        term
        for term in _SEMANTIC_REWRITE_LOANWORDS
        if term in replacement_normalized and term not in excerpt_normalized
    ]
    if not added_terms:
        return False
    source_ascii_tokens = re.findall(r"[A-Za-z0-9]+", excerpt)
    if source_ascii_tokens and any(token.casefold() in replacement_normalized for token in source_ascii_tokens):
        return True
    return not any(char in replacement for char in ("ひ", "が", "の", "を", "に", "へ", "と", "で", "な", "い", "う", "ん"))


def _is_cjk_ideograph(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff"


def _is_applicable_tts_suggestion(raw_value: Any, after: str) -> bool:
    if isinstance(raw_value, (list, dict)):
        return True
    text = str(raw_value or "").strip()
    if not text or not after.strip():
        return False
    generic_markers = [
        "必要なら",
        "必要に応じ",
        "してください",
        "修正します",
        "直します",
        "統一します",
        "指定します",
        "置き換えます",
        "調整します",
    ]
    return not any(marker in text for marker in generic_markers)


def _parse_string_list(value: str | None) -> list[Any] | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped.startswith("[") or not stripped.endswith("]"):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        try:
            import ast

            parsed = ast.literal_eval(stripped)
        except (ValueError, SyntaxError):
            return None
    return parsed if isinstance(parsed, list) else None


def _is_choice_field(field: str | None) -> bool:
    return bool(field and (field.startswith("choices") or field.startswith("tts.choiceTexts")))


def _normalize_tts_issue_location(issue: QualityIssue) -> QualityIssue:
    field = issue.location.field
    if not field:
        return issue
    lowered = field.lower()
    match = re.search(r"\d+", field)
    if "choice" in lowered:
        normalized = f"choices[{int(match.group(0))}]" if match else "choices"
    elif "explanation" in lowered:
        normalized = "explanation"
    elif "question" in lowered:
        normalized = "question"
    else:
        normalized = field
    if normalized == field:
        return issue
    return issue.model_copy(
        update={"location": issue.location.model_copy(update={"field": normalized})}
    )


def _choice_field_has_explicit_index(field: str | None) -> bool:
    return bool(field and re.search(r"\d+", field))


def _fix_prompt(file_name: str, content: dict[str, Any], issues: list[QualityIssue], max_fixes: int, *, mode: str) -> tuple[str, bool]:
    source_json = json.dumps(content, ensure_ascii=False, indent=2)
    issues_json = json.dumps([issue.model_dump() for issue in issues], ensure_ascii=False, indent=2)
    text = f"Source JSON:\n{source_json}\n\nQuality issues:\n{issues_json}"
    truncated = len(text) > MAX_FIX_INPUT_CHARS
    if truncated:
        text = text[:MAX_FIX_INPUT_CHARS]

    if mode == "tts":
        policy = """
Policy:
- Apply only reading, double_utterance, notation, and tts_text_mismatch fixes.
- Auto-apply only to tts text fields. Never change display text/question/choices/explanation.
- Null/missing tts fields are normal and must not be fixed unless the source issue points to a concrete existing tts field.
- For quiz choiceTexts, preserve index mapping. Return a choices-length array with corrected texts only at changed indexes and "" for unchanged indexes.
""".strip()
    else:
        policy = """
Policy:
- Apply no changes automatically. Return only pendingFixes for factual, style, and leak issues.
- factual must be phrased as a human-approved suggestion, not asserted truth.
- Do not change tts/audio fields here; the server will reset them only after human approval.
""".strip()

    return f"""
Return strict JSON only. Do not use markdown fences.

You are a Sokqa quality fix agent. Produce one conservative fix pass for the exact JSON file.
Do not regenerate the whole file. Only touch locations pointed to by the given issues.

{policy}
- Preserve all unrelated fields and item order.
- Return at most {max_fixes} fixes.

For document JSON:
- Auto fixes write documents[].tts.text for the target unit.
- Pending fixes propose documents[].text changes only.

For quiz JSON:
- Auto fixes write tts.questionText, tts.choiceTexts[index], or tts.explanationText depending on the issue field.
- Pending fixes propose question, choices[index], or explanation changes only.

Return this JSON shape:
{{
  "fileName": "{file_name}",
  "model": "model-name",
  "truncated": false,
  "appliedFixes": [
    {{
      "id": "auto-1",
      "category": "reading",
      "location": {{"fileName": "{file_name}", "unitId": "doc-1", "field": "text"}},
      "field": "tts.text",
      "before": "before text",
      "after": "after text",
      "sourceIssue": "source issue"
    }}
  ],
  "pendingFixes": [
    {{
      "id": "pending-1",
      "category": "style",
      "location": {{"fileName": "{file_name}", "unitId": "doc-1", "field": "text"}},
      "field": "text",
      "before": "before text",
      "suggestedAfter": "suggested text",
      "reason": "why this needs human approval",
      "sourceIssue": "source issue"
    }}
  ],
  "updatedJson": {{}}
}}

{text}
""".strip(), truncated


def _mock_fix_response(file_name: str, content: dict[str, Any], issues: list[QualityIssue], model: str, truncated: bool) -> QualityFixResponse:
    updated = copy.deepcopy(content)
    applied: list[AppliedFix] = []
    pending: list[PendingFix] = []
    auto_index = 1
    pending_index = 1
    for issue in issues:
        if issue.category in AUTO_CATEGORIES:
            before = _get_tts_field(updated, issue.location) or _get_raw_field(updated, issue.location)
            after = issue.suggestion if issue.suggestion and issue.suggestion != issue.issue else f"{before}（TTS補正）"
            _set_tts_field(updated, issue.location, after)
            applied.append(
                AppliedFix(
                    id=f"auto-{auto_index}",
                    category=issue.category,
                    location=issue.location,
                    field=_tts_field_name(updated, issue.location),
                    before=before,
                    after=after,
                    sourceIssue=issue.issue,
                )
            )
            auto_index += 1
        elif issue.category in PENDING_CATEGORIES:
            before = _get_raw_field(updated, issue.location)
            pending.append(
                PendingFix(
                    id=f"pending-{pending_index}",
                    category=issue.category,
                    location=issue.location,
                    field=issue.location.field or "text",
                    before=before,
                    suggestedAfter=issue.suggestion if issue.suggestion and issue.suggestion != issue.issue else before,
                    reason="本文変更のため承認が必要です。",
                    sourceIssue=issue.issue,
                )
            )
            pending_index += 1
    _validate_pack_json(file_name, updated)
    return QualityFixResponse(
        fileName=file_name,
        model=model,
        appliedFixes=applied,
        pendingFixes=pending,
        reRecordNeededUnits=[
            ReRecordNeededUnit(
                fileName=fix.location.fileName,
                unitId=fix.location.unitId,
                field=fix.location.field,
                category=fix.category,
            )
            for fix in applied
        ],
        updatedJson=updated,
        truncated=truncated,
    )


def _validate_pack_json(file_name: str, content: dict[str, Any]) -> dict[str, Any]:
    kind = content.get("type")
    if kind == "document":
        normalized = SokqaDocumentPack.model_validate(content).model_dump(exclude_none=True)
    elif kind == "quiz":
        normalized = SokqaQuizPack.model_validate(content).model_dump(exclude_none=True)
    else:
        raise ValueError("content type must be document or quiz")
    result = validate_files([GeneratedFile(name=file_name, kind=kind, content=normalized)])
    if not result.valid:
        raise ValueError("; ".join(error.message for error in result.errors))
    return normalized


def _get_raw_field(content: dict[str, Any], location: QualityLocation) -> str:
    unit = _find_unit(content, location.unitId)
    if not unit:
        return ""
    field = location.field or ("text" if content.get("type") == "document" else "question")
    if _is_choice_field(field):
        choices = unit.get("choices") or []
        index = _choice_index(field)
        if 0 <= index < len(choices):
            return str(choices[index])
        return str(choices[0]) if choices else ""
    return str(unit.get(field) or "")


def _set_raw_field(content: dict[str, Any], location: QualityLocation, value: str) -> bool:
    unit = _find_unit(content, location.unitId)
    if not unit:
        return False
    field = location.field or ("text" if content.get("type") == "document" else "question")
    if _is_choice_field(field):
        choices = unit.get("choices")
        if not isinstance(choices, list):
            return False
        index = _choice_index(field)
        if not 0 <= index < len(choices):
            return False
        choices[index] = value
        return True
    if field not in unit:
        return False
    unit[field] = value
    return True


def _reset_unit_tts(content: dict[str, Any], location: QualityLocation) -> None:
    unit = _find_unit(content, location.unitId)
    if not unit:
        return
    unit.pop("tts", None)


def _get_tts_field(content: dict[str, Any], location: QualityLocation) -> str:
    unit = _find_unit(content, location.unitId)
    if not unit:
        return ""
    tts = unit.get("tts") or {}
    if content.get("type") == "document":
        return str(tts.get("text") or "")
    field = location.field or "question"
    if _is_choice_field(field):
        choices = tts.get("choiceTexts") or []
        index = _choice_index(field)
        if 0 <= index < len(choices):
            return str(choices[index])
        return ""
    if field == "explanation":
        return str(tts.get("explanationText") or "")
    return str(tts.get("questionText") or "")


def _set_tts_field(content: dict[str, Any], location: QualityLocation, value: str) -> bool:
    unit = _find_unit(content, location.unitId)
    if not unit:
        return False
    if content.get("type") == "document":
        tts = dict(unit.get("tts") or {})
        tts["text"] = value
        unit["tts"] = DocumentTts.model_validate(tts).model_dump(exclude_none=True)
        return True

    tts = dict(unit.get("tts") or {})
    field = location.field or "question"
    if _is_choice_field(field):
        choice_count = max(4, len(unit.get("choices") or []))
        existing_choice_texts = tts.get("choiceTexts")
        choices = list(existing_choice_texts) if isinstance(existing_choice_texts, list) else [""] * choice_count
        index = _choice_index(field)
        if index >= choice_count:
            return False
        while len(choices) < choice_count:
            choices.append("")
        choices[index] = value
        tts["choiceTexts"] = choices[:choice_count]
    elif field == "explanation":
        tts["explanationText"] = value
    else:
        tts["questionText"] = value
    unit["tts"] = QuizTts.model_validate(tts).model_dump(exclude_none=True)
    return True


def _tts_field_name(content: dict[str, Any], location: QualityLocation) -> str:
    if content.get("type") == "document":
        return "tts.text"
    field = location.field or "question"
    if _is_choice_field(field):
        return f"tts.choiceTexts[{_choice_index(field)}]"
    if field == "explanation":
        return "tts.explanationText"
    return "tts.questionText"


def _find_unit(content: dict[str, Any], unit_id: str | None) -> dict[str, Any] | None:
    collection = content.get("documents") if content.get("type") == "document" else content.get("questions")
    if not isinstance(collection, list):
        return None
    if unit_id is None and collection:
        return collection[0]
    for unit in collection:
        if isinstance(unit, dict) and unit.get("id") == unit_id:
            return unit
    return None


def _choice_index(field: str | None) -> int:
    if not field:
        return 0
    match = field.replace("tts.choiceTexts", "").replace("choiceTexts", "").replace("choices", "").strip("[] .:_")
    try:
        return max(0, min(3, int(match)))
    except ValueError:
        return 0
