from __future__ import annotations

import copy
import json
import logging
import time
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
from app.schemas.sokqa import DocumentTts, GeneratedFile, PackManifest, QuizTts, SokqaDocumentPack, SokqaQuizPack
from app.schemas.request import TtsRecordingTarget
from app.services.gemini_client import GeminiClient
from app.services.pack_metadata import build_pack_version_metadata
from app.services.quality_checker import _generate_json_with_retry
from app.services.storage_client import StorageClient
from app.services.tts_recording_api import _identity_from_storage_prefix, _url_basename, load_target_pack
from app.services.validator import validate_files


AUTO_CATEGORIES = {"reading", "double_utterance", "notation", "tts_text_mismatch"}
PENDING_CATEGORIES = {"factual", "style", "leak"}
MAX_FIX_INPUT_CHARS = 30000
logger = logging.getLogger(__name__)


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
    return _generate_tts_fix_without_llm(target, [issue for issue in issues if issue.category in AUTO_CATEGORIES], max_fixes)


def generate_tts_fix_with_llm(target: TtsRecordingTarget, issues: list[QualityIssue], max_fixes: int = 50) -> QualityFixResponse:
    return _generate_quality_fix(target, [issue for issue in issues if issue.category in AUTO_CATEGORIES], max_fixes, mode="tts")


def generate_text_fix(target: TtsRecordingTarget, issues: list[QualityIssue], max_fixes: int = 50) -> QualityFixResponse:
    return _generate_quality_fix(target, [issue for issue in issues if issue.category in PENDING_CATEGORIES], max_fixes, mode="text")


def _generate_quality_fix(target: TtsRecordingTarget, issues: list[QualityIssue], max_fixes: int, *, mode: str) -> QualityFixResponse:
    loaded = load_target_pack(target)
    if mode == "tts":
        _validate_tts_fix_input(loaded.file.content)
    settings = get_settings()
    model = settings.fix_model
    limited_issues = issues[:max_fixes]

    if settings.gemini_provider == "mock":
        return _mock_fix_response(loaded.file.name, loaded.file.content, limited_issues, model, len(issues) > max_fixes)

    prompt, input_truncated = _fix_prompt(loaded.file.name, loaded.file.content, limited_issues, max_fixes, mode=mode)
    llm_start = time.perf_counter()
    llm_attempts = {"count": 0}

    def _call_gemini() -> dict[str, Any]:
        llm_attempts["count"] += 1
        return GeminiClient().generate_json(prompt, model=model, temperature=0.2)

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
        )
        _validate_pack_json(loaded.file.name, response.updatedJson)
        return response
    except (TypeError, ValidationError, ValueError) as exc:
        raise QualityFixError(f"quality fix response validation failed: {exc}") from exc


def _generate_tts_fix_without_llm(
    target: TtsRecordingTarget,
    issues: list[QualityIssue],
    max_fixes: int,
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
        location = issue.location
        if location.fileName != loaded.file.name:
            skipped += 1
            logger.info("tts fix skipped issue: file mismatch %s != %s", location.fileName, loaded.file.name)
            continue
        if not _find_unit(loaded.file.content, location.unitId):
            skipped += 1
            logger.info("tts fix skipped issue: unresolved location %s", location.model_dump())
            continue

        before = _get_tts_field(loaded.file.content, location) or _get_raw_field(loaded.file.content, location)
        after = _fix_after_text(issue.suggestion, location)
        if after is None or not _is_applicable_tts_suggestion(issue.suggestion, after):
            unapplied.append(
                _unapplied_fix(
                    issue,
                    loaded.file.content,
                    index,
                    reason="suggestion が空、または適用可能な修正後テキストではありません。",
                )
            )
            continue

        if not _set_tts_field(updated_json, location, after):
            skipped += 1
            logger.info("tts fix skipped issue: failed to set tts field %s", location.model_dump())
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
        if _set_raw_field(final_json, fix.location, fix.suggestedAfter):
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
    metadata = build_pack_version_metadata(creator_id, content_id)
    storage = storage_client or StorageClient()
    storage.copy_prefix(loaded.storage_prefix, metadata.storage_prefix)
    asset_base_url = storage.public_url_for_prefix(metadata.storage_prefix).rstrip("/")

    generated_files: list[GeneratedFile] = []
    response_files: list[QualityFixSaveFile] = []
    for file in files:
        content = copy.deepcopy(file.content)
        if content.get("type") != file.kind:
            raise ValueError(f"{file.name} kind does not match content type")
        content["assetBaseUrl"] = asset_base_url
        _validate_pack_json(file.name, content)
        generated_files.append(GeneratedFile(name=file.name, kind=file.kind, content=content))
        response_files.append(QualityFixSaveFile(name=file.name, kind=file.kind, content=content))

    storage.save_files(content_id, generated_files, metadata.storage_prefix)
    _complete_fix_snapshot(
        storage,
        content_id,
        loaded.storage_prefix,
        metadata.storage_prefix,
        metadata.version_id,
        metadata.build_id,
        metadata.generated_at,
        {file.name for file in files},
    )
    return QualityFixSaveResponse(
        newVersionId=metadata.version_id,
        newAssetBaseUrl=asset_base_url,
        storagePrefix=metadata.storage_prefix,
        files=response_files,
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


def _fix_response_from_data(
    data: dict[str, Any],
    *,
    file_name: str,
    original_json: dict[str, Any],
    model: str,
    max_fixes: int,
    input_truncated: bool,
) -> QualityFixResponse:
    raw_applied = data.get("appliedFixes", [])
    raw_pending = data.get("pendingFixes", [])
    updated_json = data.get("updatedJson")
    if not isinstance(raw_applied, list) or not isinstance(raw_pending, list) or not isinstance(updated_json, dict):
        raise ValueError("response must contain appliedFixes, pendingFixes, and updatedJson")
    updated_json = copy.deepcopy(original_json)
    applied = [
        fix
        for fix in (_normalize_applied_fix(item, original_json, updated_json, file_name, index) for index, item in enumerate(raw_applied, start=1))
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
    _set_tts_field(updated_json, location, after)
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
    return PendingFix(
        id=str(raw.get("id") or f"pending-{index}"),
        category=category,
        location=location,
        field=str(raw.get("field") or location.field or "text"),
        before=_get_raw_field(original_json, location),
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
- For quiz choiceTexts, preserve index mapping. If one choice text changes, return a full 4-item choiceTexts array in updatedJson.
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


def _validate_pack_json(file_name: str, content: dict[str, Any]) -> None:
    kind = content.get("type")
    if kind == "document":
        SokqaDocumentPack.model_validate(content)
    elif kind == "quiz":
        SokqaQuizPack.model_validate(content)
    else:
        raise ValueError("content type must be document or quiz")
    result = validate_files([GeneratedFile(name=file_name, kind=kind, content=content)])
    if not result.valid:
        raise ValueError("; ".join(error.message for error in result.errors))


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
    if content.get("type") == "document":
        current = dict(unit.get("tts") or {})
        current.pop("text", None)
        current.pop("audioUrl", None)
        current.pop("audioPath", None)
        current["ttsNeedsRefresh"] = True
        unit["tts"] = DocumentTts.model_validate(current).model_dump(exclude_none=True)
        return

    current = dict(unit.get("tts") or {})
    for key in (
        "questionText",
        "choiceTexts",
        "answerText",
        "explanationText",
        "questionAudioUrl",
        "choiceAudioUrls",
        "explanationAudioUrl",
        "questionAudioPath",
        "choiceAudioPaths",
        "explanationAudioPath",
    ):
        current.pop(key, None)
    current["ttsNeedsRefresh"] = True
    unit["tts"] = QuizTts.model_validate(current).model_dump(exclude_none=True)


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
        choices = list(tts.get("choiceTexts") or unit.get("choices") or [])
        index = _choice_index(field)
        if index >= max(4, len(unit.get("choices") or [])):
            return False
        while len(choices) < 4:
            choices.append("")
        choices[index] = value
        tts["choiceTexts"] = choices[:4]
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


def _complete_fix_snapshot(
    storage: StorageClient,
    content_id: str,
    source_prefix: str,
    target_prefix: str,
    version_id: str,
    build_id: str,
    generated_at: str,
    updated_pack_names: set[str],
) -> None:
    try:
        manifest = PackManifest.model_validate(storage.load_json_file(content_id, "manifest.json", source_prefix))
    except Exception:
        return

    base_url = storage.public_url_for_prefix(target_prefix).rstrip("/")
    snapshot_files: list[GeneratedFile] = []
    for item in manifest.items:
        pack_name = _url_basename(item.url)
        if not pack_name:
            continue
        item.url = f"{base_url}/{pack_name}"
        if pack_name in updated_pack_names:
            continue
        try:
            content = storage.load_json_file(content_id, pack_name, source_prefix)
        except Exception:
            continue
        if content.get("type") in {"document", "quiz"}:
            content["assetBaseUrl"] = base_url
            snapshot_files.append(GeneratedFile(name=pack_name, kind=content["type"], content=content))

    if snapshot_files:
        storage.save_files(content_id, snapshot_files, target_prefix)

    manifest.versionId = version_id
    manifest.buildId = build_id
    manifest.generatedAt = generated_at
    storage.save_files(
        content_id,
        [
            GeneratedFile(
                name="manifest.json",
                kind="manifest",
                content=manifest.model_dump(exclude_none=True),
            )
        ],
        target_prefix,
    )
