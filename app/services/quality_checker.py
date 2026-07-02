from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from collections import Counter
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.schemas.quality import QualityCheckResponse, QualityIssue
from app.schemas.request import TtsRecordingTarget
from app.services.gemini_client import GeminiClient
from app.services.language_detection import choice_set_language_state, language_script, leading_script
from app.services.multilingual_detection import MultilingualStatus, detect_multilingual
from app.services.tts_recording_api import load_target_pack


MAX_QUALITY_INPUT_CHARS = 30000
TEXT_QUALITY_CATEGORIES = {"factual", "style", "leak"}
TTS_QUALITY_CATEGORIES = {"reading", "double_utterance", "notation", "tts_text_mismatch"}
FULL_REPLACE_CATEGORIES = {"factual", "style", "leak", "tts_text_mismatch"}
_logger = logging.getLogger(__name__)

_UNSPOKEN_READING_SYMBOLS = set("「」『』（）()・、。，．,. 　\t\r\n")
_UNSPOKEN_SYMBOL_NAMES = {
    "かぎ括弧",
    "鍵括弧",
    "カギ括弧",
    "丸括弧",
    "括弧",
    "中黒",
    "句読点",
    "全角スペース",
}
_PLACEHOLDER_KEYWORD_PATTERNS = (
    r"placeholder",
    r"プレースホルダー",
    r"未確定",
    r"下書き",
    r"ドラフト",
    r"masked name",
    r"generic name",
    r"authoring comment",
)
_FRAGMENT_TRAILING_CONNECTIVES = ("ので", "ため", "て", "で")


class QualityCheckError(RuntimeError):
    pass


def check_pack_quality(target: TtsRecordingTarget, max_issues: int = 50) -> QualityCheckResponse:
    return check_text_quality(target, max_issues)


def check_text_quality(target: TtsRecordingTarget, max_issues: int = 50) -> QualityCheckResponse:
    return _check_pack_quality(target, max_issues, mode="text")


def check_tts_quality(target: TtsRecordingTarget, max_issues: int = 50) -> QualityCheckResponse:
    return _check_pack_quality(target, max_issues, mode="tts")


def _check_pack_quality(target: TtsRecordingTarget, max_issues: int, *, mode: str) -> QualityCheckResponse:
    loaded = load_target_pack(target)
    settings = get_settings()
    model = settings.quality_model

    deterministic_issues = _deterministic_tts_issues(loaded.file.name, loaded.file.content) if mode == "tts" else []
    multilingual_status = detect_multilingual(loaded.file.content)
    allow_language_tags = multilingual_status == MultilingualStatus.MULTILINGUAL

    if settings.gemini_provider == "mock":
        response = _mock_quality_response(loaded.file.name, model, max_issues, mode=mode)
        if mode == "tts":
            response.issues = (deterministic_issues + response.issues)[:max_issues]
        return response

    prompt, input_truncated = _quality_prompt(
        loaded.file.name,
        loaded.file.content,
        max_issues,
        mode=mode,
        multilingual=allow_language_tags,
    )
    try:
        data = _generate_json_with_retry(lambda: GeminiClient().generate_json(prompt, model=model))
    except Exception as exc:
        raise QualityCheckError(f"quality check LLM call failed: {exc}") from exc

    try:
        response = _quality_response_from_data(
            data,
            file_name=loaded.file.name,
            model=model,
            max_issues=max_issues,
            allowed_categories=TEXT_QUALITY_CATEGORIES if mode == "text" else TTS_QUALITY_CATEGORIES,
            suppress_tts_null_issues=mode == "tts",
            input_truncated=input_truncated,
            source_content=loaded.file.content,
        )
        if mode == "tts":
            llm_issues = response.issues
            response.issues = (deterministic_issues + llm_issues)[:max_issues]
            response.truncated = response.truncated or len(deterministic_issues) + len(llm_issues) > max_issues
        return response
    except (TypeError, ValidationError, ValueError) as exc:
        raise QualityCheckError(f"quality check response validation failed: {exc}") from exc
    except Exception as exc:
        raise QualityCheckError(f"quality check unexpected error: {exc}") from exc


def _generate_json_with_retry(
    generate: Callable[[], Any],
    *,
    attempts: int = 3,
    initial_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return generate()
        except Exception as exc:
            last_error = exc
            _logger.warning("quality_check.retry attempt=%s error=%s", attempt, repr(exc))
            if attempt == attempts - 1:
                break
            sleep(initial_delay * (2**attempt))
    raise last_error or QualityCheckError("quality check LLM call failed")


def _quality_response_from_data(
    data: Any,
    *,
    file_name: str,
    model: str,
    max_issues: int,
    allowed_categories: set[str] | None = None,
    suppress_tts_null_issues: bool = False,
    input_truncated: bool = False,
    source_content: dict[str, Any] | None = None,
) -> QualityCheckResponse:
    raw_issues, response_truncated, response_file_name = _quality_response_parts(data, file_name)
    if not isinstance(raw_issues, list):
        raise ValueError("response must contain an issues array")

    issues: list[QualityIssue] = []
    for item in raw_issues:
        if isinstance(item, dict):
            suggestion = item.get("suggestion")
            if isinstance(suggestion, str) and not suggestion.strip():
                continue
        issues.append(_normalize_quality_issue_location(QualityIssue.model_validate(item)))
    before_count = len(issues)
    issues = [issue for issue in issues if not _is_obvious_meta_suggestion(issue)]
    filtered_count = before_count - len(issues)
    if filtered_count:
        _logger.info("quality_check.filtered_meta_suggestion_issues count=%s file=%s", filtered_count, file_name)
    if allowed_categories is not None:
        issues = [issue for issue in issues if issue.category in allowed_categories]
    if allowed_categories == TEXT_QUALITY_CATEGORIES:
        before_count = len(issues)
        issues = [issue for issue in issues if not _is_non_issue_placeholder_report(issue)]
        filtered_count = before_count - len(issues)
        if filtered_count:
            _logger.info("quality_check.filtered_non_issue_placeholder_reports count=%s file=%s", filtered_count, file_name)
    if suppress_tts_null_issues:
        issues = [issue for issue in issues if not _is_tts_null_issue(issue)]
    if allowed_categories == TTS_QUALITY_CATEGORIES:
        before_count = len(issues)
        issues = [issue for issue in issues if not _is_unspoken_symbol_reading_issue(issue)]
        filtered_count = before_count - len(issues)
        if filtered_count:
            _logger.info("quality_check.filtered_unspoken_symbol_reading_issues count=%s file=%s", filtered_count, file_name)
        if source_content is not None:
            before_count = len(issues)
            issues = [issue for issue in issues if not _is_already_corrected_reading_issue(issue, source_content)]
            filtered_count = before_count - len(issues)
            if filtered_count:
                _logger.info("quality_check.filtered_already_corrected_reading_issues count=%s file=%s", filtered_count, file_name)
    issues = _filter_fragment_suggestions(issues, file_name=file_name)
    issues = _filter_trailing_japanese_period_only_suggestions(
        issues,
        file_name=file_name,
        source_content=source_content,
    )
    issues = _filter_same_as_original_suggestions(issues, file_name=file_name, source_content=source_content)
    before_count = len(issues)
    issues = _dedupe_quality_issues(issues)
    filtered_count = before_count - len(issues)
    if filtered_count:
        _logger.info("quality_check.filtered_duplicate_issues count=%s file=%s", filtered_count, file_name)
    truncated = response_truncated or input_truncated or len(issues) > max_issues
    return QualityCheckResponse(
        fileName=response_file_name,
        model=model,
        issues=issues[:max_issues],
        truncated=truncated,
    )


def _quality_response_parts(data: Any, file_name: str) -> tuple[Any, bool, str]:
    if isinstance(data, list):
        return data, False, file_name
    if isinstance(data, dict):
        return data.get("issues"), bool(data.get("truncated")), str(data.get("fileName") or file_name)
    return [], False, file_name


def _normalize_quality_field(field: str | None) -> str | None:
    if not field:
        return field
    lowered = field.lower()
    match = re.search(r"\d+", field)
    if "choice" in lowered:
        return f"choices[{int(match.group(0))}]" if match else "choices"
    if "explanation" in lowered:
        return "explanation"
    if "question" in lowered:
        return "question"
    return "text" if lowered in {"text", "tts.text"} else field


def _normalize_quality_issue_location(issue: QualityIssue) -> QualityIssue:
    return issue.model_copy(
        update={
            "location": issue.location.model_copy(
                update={"field": _normalize_quality_field(issue.location.field)}
            )
        }
    )


def _deterministic_tts_issues(file_name: str, content: dict[str, Any]) -> list[QualityIssue]:
    if content.get("type") != "quiz":
        return []
    pack_language = str(content.get("language") or "ja")
    learning_language = content.get("learningLanguage")
    if not learning_language:
        return []
    choice_mode = str(content.get("choiceLanguageMode") or "auto")
    pack_script = language_script(pack_language)
    learning_script = language_script(str(learning_language))
    issues: list[QualityIssue] = []
    for question in content.get("questions") or []:
        if not isinstance(question, dict):
            continue
        unit_id = str(question.get("id") or "")
        choices = [str(choice) for choice in question.get("choices") or []]
        tts = question.get("tts") if isinstance(question.get("tts"), dict) else {}
        choice_texts = tts.get("choiceTexts")
        if choice_texts is not None and (
            not isinstance(choice_texts, list) or len(choice_texts) != len(choices)
        ):
            issues.append(
                _quality_issue(
                    file_name,
                    unit_id,
                    "choices",
                    "choiceTexts",
                    "choiceTexts の配列長が choices と一致していません。",
                    "choices と同じ長さの配列に修正してください。",
                    category="notation",
                )
            )
            choice_texts = choice_texts if isinstance(choice_texts, list) else []

        state = choice_set_language_state(choices, pack_language, str(learning_language))
        if choice_mode == "auto" and state == "mixed":
            issues.append(
                _quality_issue(
                    file_name,
                    unit_id,
                    "choices",
                    " / ".join(choices),
                    "auto設定ですが、1問内の4択にパック言語と学習言語が混在しています。",
                    "4択を同じ言語に統一してください。",
                    category="notation",
                )
            )
        elif choice_mode == "learning" and state in {"pack", "mixed"}:
            issues.append(
                _quality_issue(
                    file_name,
                    unit_id,
                    "choices",
                    " / ".join(choices),
                    "選択肢表示方式が学習言語ですが、選択肢がパック言語になっています。",
                    f"4択を学習言語（{learning_language}）へ統一してください。",
                    category="tts_text_mismatch",
                )
            )
        elif choice_mode == "pack" and state in {"learning", "mixed"}:
            issues.append(
                _quality_issue(
                    file_name,
                    unit_id,
                    "choices",
                    " / ".join(choices),
                    "選択肢表示方式がパック言語ですが、選択肢が学習言語になっています。",
                    f"4択をパック言語（{pack_language}）へ統一してください。",
                    category="tts_text_mismatch",
                )
            )

        if pack_script == learning_script:
            continue
        tag = f"[{_speech_code(str(learning_language))}]"
        for index, choice in enumerate(choices):
            if leading_script(choice) != learning_script:
                continue
            current = (
                str(choice_texts[index] or "")
                if isinstance(choice_texts, list) and index < len(choice_texts)
                else ""
            )
            if not current.startswith(tag):
                issue_text = (
                    "学習言語の選択肢に必要な言語タグがありません。"
                    if current
                    else "学習言語の選択肢に必要な choiceTexts がありません。"
                )
                issues.append(
                    _quality_issue(
                        file_name,
                        unit_id,
                        f"choices[{index}]",
                        choice,
                        issue_text,
                        f"{tag}{choice}",
                    )
                )
    return issues


def _speech_code(language: str) -> str:
    from app.schemas.common import default_speech_language_code

    return default_speech_language_code(language)


def _quality_issue(
    file_name: str,
    unit_id: str,
    field: str,
    excerpt: str,
    issue: str,
    suggestion: str,
    *,
    category: str = "reading",
) -> QualityIssue:
    return QualityIssue.model_validate(
        {
            "category": category,
            "severity": "high",
            "confidence": 1.0,
            "location": {"fileName": file_name, "unitId": unit_id, "field": field},
            "excerpt": excerpt or field,
            "issue": issue,
            "suggestion": suggestion,
        }
    )


def _is_tts_null_issue(issue: QualityIssue) -> bool:
    text = " ".join(
        [
            issue.location.field or "",
            issue.excerpt,
            issue.issue,
            issue.suggestion,
        ]
    ).lower()
    audio_markers = [
        "audiopath",
        "audiourl",
        "choiceaudiopaths",
        "choiceaudiourls",
        "questionaudiopath",
        "questionaudiourl",
        "explanationaudiopath",
        "explanationaudiourl",
        "録音",
        "未録音",
        "音声",
    ]
    null_markers = ["null", "none", "missing", "empty", "未設定", "欠落", "空"]
    return any(marker in text for marker in audio_markers) and any(marker in text for marker in null_markers)


def _is_unspoken_symbol_reading_issue(issue: QualityIssue) -> bool:
    if issue.category != "reading":
        return False

    excerpt = issue.excerpt.strip()
    if not excerpt:
        return False
    if _is_unspoken_symbol_only(excerpt):
        return True

    compact_excerpt = "".join(excerpt.split())
    if compact_excerpt in _UNSPOKEN_SYMBOL_NAMES:
        return True

    return False


def _is_unspoken_symbol_only(text: str) -> bool:
    return bool(text.strip()) and all(char in _UNSPOKEN_READING_SYMBOLS for char in text)


def _is_already_corrected_reading_issue(issue: QualityIssue, content: dict[str, Any]) -> bool:
    if issue.category != "reading" or not issue.suggestion.strip():
        return False
    tts_text = _tts_text_for_issue_location(content, issue)
    if not tts_text:
        return False
    normalized_tts = _normalize_reading_match_text(tts_text)
    normalized_suggestion = _normalize_reading_match_text(issue.suggestion)
    return bool(normalized_suggestion and normalized_suggestion in normalized_tts)


def _is_obvious_meta_suggestion(issue: QualityIssue) -> bool:
    suggestion = unicodedata.normalize("NFKC", issue.suggestion).strip()
    if not suggestion:
        return False
    meta_patterns = (
        r"TTSが.+読み上げられるよう.+指示.+必要です",
        r"AIへの指示文",
        r"メタコメント",
        r"説明[・:]",
        r"注釈[・:]",
        r"理由[・:]",
        r"以下のように修正",
    )
    return any(re.search(pattern, suggestion) for pattern in meta_patterns)


def _is_non_issue_placeholder_report(issue: QualityIssue) -> bool:
    if issue.category not in {"leak", "style"}:
        return False
    if not _is_placeholder_related_issue(issue):
        return False
    return not _is_unresolved_placeholder_excerpt(issue.excerpt)


def _is_placeholder_related_issue(issue: QualityIssue) -> bool:
    combined = unicodedata.normalize("NFKC", f"{issue.excerpt}\n{issue.issue}\n{issue.suggestion}")
    if any(re.search(pattern, combined, re.IGNORECASE) for pattern in _PLACEHOLDER_KEYWORD_PATTERNS):
        return True
    return bool(re.search(r"[◯○〜\[\]_＿]|(?<![A-Za-z0-9])X{3,}(?![A-Za-z0-9])|\bCompany Name\b", combined, re.IGNORECASE))


def _is_unresolved_placeholder_excerpt(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text).strip()
    if not normalized:
        return False
    if _is_intended_blank_placeholder(normalized):
        return False
    if _has_valid_tilde_usage(normalized):
        return False
    if re.search(r"[◯○]{2,}", normalized):
        return True
    if re.search(r"(?<![A-Za-z0-9])X{3,}(?![A-Za-z0-9])", normalized, re.IGNORECASE):
        return True
    if re.search(r"\[\s*(?:name|company\s+name|first\s+name|last\s+name)\s*\]", normalized, re.IGNORECASE):
        return True
    if re.search(r"\bCompany Name\b", normalized, re.IGNORECASE):
        return True
    if "〜" in normalized:
        return True
    return False


def _is_intended_blank_placeholder(text: str) -> bool:
    return bool(re.fullmatch(r"[_＿]{3,}", text))


def _has_valid_tilde_usage(text: str) -> bool:
    compact = "".join(unicodedata.normalize("NFKC", text).split())
    if not compact or "〜" not in compact:
        return False
    if re.fullmatch(r"\d+〜\d+", compact):
        return True
    return "〜てください" in compact


def _tts_text_for_issue_location(content: dict[str, Any], issue: QualityIssue) -> str:
    unit = _find_quality_unit(content, issue.location.unitId)
    if not unit:
        return ""
    tts = unit.get("tts") or {}
    if not isinstance(tts, dict):
        return ""
    if content.get("type") == "document":
        return str(tts.get("text") or "")

    field = issue.location.field or "question"
    if _is_quality_choice_field(field):
        index = _quality_choice_index(field)
        if index is None:
            index = _infer_choice_index_from_excerpt(unit, issue.excerpt)
        choices = tts.get("choiceTexts") or []
        if index is not None and 0 <= index < len(choices):
            return str(choices[index] or "")
        return ""
    if "explanation" in field:
        return str(tts.get("explanationText") or "")
    return str(tts.get("questionText") or "")


def _find_quality_unit(content: dict[str, Any], unit_id: str | None) -> dict[str, Any] | None:
    collection = content.get("documents") if content.get("type") == "document" else content.get("questions")
    if not isinstance(collection, list):
        return None
    if unit_id is None:
        return collection[0] if collection and isinstance(collection[0], dict) else None
    for unit in collection:
        if isinstance(unit, dict) and unit.get("id") == unit_id:
            return unit
    return None


def _source_text_for_issue_location(content: dict[str, Any], issue: QualityIssue) -> str:
    unit = _find_quality_unit(content, issue.location.unitId)
    if not unit:
        return ""
    if content.get("type") == "document":
        return str(unit.get("text") or "")

    field = issue.location.field or "question"
    if _is_quality_choice_field(field):
        index = _quality_choice_index(field)
        if index is None:
            index = _infer_choice_index_from_excerpt(unit, issue.excerpt)
        choices = unit.get("choices") or []
        if index is not None and 0 <= index < len(choices):
            return str(choices[index] or "")
        return ""
    if "explanation" in field:
        return str(unit.get("explanation") or "")
    return str(unit.get("question") or "")


def _is_quality_choice_field(field: str | None) -> bool:
    return bool(field and ("choice" in field.lower() or field.startswith("choices")))


def _quality_choice_index(field: str | None) -> int | None:
    if not field:
        return None
    match = re.search(r"\d+", field)
    if not match:
        return None
    return int(match.group(0))


def _infer_choice_index_from_excerpt(unit: dict[str, Any], excerpt: str) -> int | None:
    normalized_excerpt = _normalize_reading_match_text(excerpt)
    if not normalized_excerpt:
        return None
    for index, choice in enumerate(unit.get("choices") or []):
        if normalized_excerpt in _normalize_reading_match_text(str(choice)):
            return index
    return None


def _normalize_reading_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if not char.isspace() and char not in "、。，．,.「」『』（）()[]【】")


def _normalize_issue_equality_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(char for char in normalized if not char.isspace())


def _split_trailing_japanese_period(value: str) -> tuple[str, bool]:
    normalized = unicodedata.normalize("NFKC", value).strip()
    if normalized.endswith("。"):
        return normalized[:-1], True
    return normalized, False


def _has_trailing_japanese_period_only_difference(left: str, right: str) -> bool:
    left_base, left_has_period = _split_trailing_japanese_period(left)
    right_base, right_has_period = _split_trailing_japanese_period(right)
    return bool(left_base and left_base == right_base and left_has_period != right_has_period)


def _is_same_excerpt_and_suggestion_issue(issue: QualityIssue) -> bool:
    normalized_excerpt = _normalize_issue_equality_text(issue.excerpt)
    normalized_suggestion = _normalize_issue_equality_text(issue.suggestion)
    return bool(normalized_excerpt and normalized_excerpt == normalized_suggestion)


def _is_same_source_and_suggestion_issue(issue: QualityIssue, content: dict[str, Any]) -> bool:
    source_text = _source_text_for_issue_location(content, issue)
    normalized_source = _normalize_issue_equality_text(source_text)
    normalized_suggestion = _normalize_issue_equality_text(issue.suggestion)
    return bool(normalized_source and normalized_source == normalized_suggestion)


def _fragment_reject_reason(issue: QualityIssue) -> str | None:
    if issue.category not in FULL_REPLACE_CATEGORIES:
        return None
    suggestion = unicodedata.normalize("NFKC", issue.suggestion).strip()
    if not suggestion or suggestion.endswith("。"):
        return None
    if suggestion.endswith("、"):
        return "trailing_comma"
    if any(suggestion.endswith(suffix) for suffix in _FRAGMENT_TRAILING_CONNECTIVES):
        return "trailing_connective"
    return None


def _filter_fragment_suggestions(issues: list[QualityIssue], *, file_name: str) -> list[QualityIssue]:
    reject_reasons: Counter[str] = Counter()
    filtered: list[QualityIssue] = []
    for issue in issues:
        reason = _fragment_reject_reason(issue)
        if reason is None:
            filtered.append(issue)
            continue
        reject_reasons[reason] += 1
    for reason, count in sorted(reject_reasons.items()):
        _logger.info(
            "quality_check.rejected_fragment_suggestions count=%s file=%s reason=%s",
            count,
            file_name,
            reason,
        )
    return filtered


def _is_same_as_original_suggestion_issue(
    issue: QualityIssue,
    content: dict[str, Any] | None,
) -> bool:
    if _is_same_excerpt_and_suggestion_issue(issue):
        return True
    if content is None:
        return False
    return _is_same_source_and_suggestion_issue(issue, content)


def _is_trailing_japanese_period_only_suggestion_issue(
    issue: QualityIssue,
    content: dict[str, Any] | None,
) -> bool:
    if issue.category not in TTS_QUALITY_CATEGORIES:
        return False
    if _has_trailing_japanese_period_only_difference(issue.excerpt, issue.suggestion):
        return True
    if content is None:
        return False
    source_text = _source_text_for_issue_location(content, issue)
    return bool(source_text) and _has_trailing_japanese_period_only_difference(source_text, issue.suggestion)


def _filter_trailing_japanese_period_only_suggestions(
    issues: list[QualityIssue],
    *,
    file_name: str,
    source_content: dict[str, Any] | None,
) -> list[QualityIssue]:
    filtered: list[QualityIssue] = []
    rejected_count = 0
    for issue in issues:
        if _is_trailing_japanese_period_only_suggestion_issue(issue, source_content):
            rejected_count += 1
            continue
        filtered.append(issue)
    if rejected_count:
        _logger.info(
            "quality_check.rejected_trailing_period_only_suggestions count=%s file=%s reason=trailing_period_only",
            rejected_count,
            file_name,
        )
    return filtered


def _filter_same_as_original_suggestions(
    issues: list[QualityIssue],
    *,
    file_name: str,
    source_content: dict[str, Any] | None,
) -> list[QualityIssue]:
    filtered: list[QualityIssue] = []
    rejected_count = 0
    for issue in issues:
        if _is_same_as_original_suggestion_issue(issue, source_content):
            rejected_count += 1
            continue
        filtered.append(issue)
    if rejected_count:
        _logger.info(
            "quality_check.rejected_same_as_original_suggestions count=%s file=%s reason=same_as_original",
            rejected_count,
            file_name,
        )
    return filtered


def _quality_issue_dedupe_key(issue: QualityIssue) -> tuple[str, str, str]:
    return (
        str(issue.location.unitId or ""),
        str(issue.location.field or ""),
        unicodedata.normalize("NFKC", issue.excerpt).strip(),
    )


def _dedupe_quality_issues(issues: list[QualityIssue]) -> list[QualityIssue]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[QualityIssue] = []
    for issue in issues:
        key = _quality_issue_dedupe_key(issue)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


def _quality_prompt(
    file_name: str,
    content: dict[str, Any],
    max_issues: int,
    *,
    mode: str,
    multilingual: bool = True,
) -> tuple[str, bool]:
    source_json = json.dumps(content, ensure_ascii=False, indent=2)
    truncated = len(source_json) > MAX_QUALITY_INPUT_CHARS
    if truncated:
        source_json = source_json[:MAX_QUALITY_INPUT_CHARS]

    if mode == "tts":
        language_tag_rules = (
            """
- Language tags are allowed only for multilingual files.
- Use language tags only when the text clearly contains a non-default language span.
- The canonical language-tag format is a switch tag sequence such as [en-US]English[ja-JP]. Each language span continues until the next tag, and each new item starts in the default pack language automatically.
""".strip()
            if multilingual
            else """
- This is a normal (non-multilingual) file. Never suggest adding language tags such as [ja-JP], [en-US], or [id-ID].
- Do not output any [xx-XX] style language tag in suggestion.
""".strip()
        )
        category_block = """
Categories:
- reading: TTS misreading risks such as acronyms, code terms, symbols, or mixed-language spans.
- double_utterance: duplicated wording in tts fields that would be spoken twice or sounds redundant.
- notation: inconsistent spoken notation/readings. Do not report visual notation issues here.
- tts_text_mismatch: clear semantic mismatch between source text and tts text. Only report when meaning, answer, quantity, negation, or proper nouns clearly differ. Do not report kana conversion, reading correction, language tags, or punctuation differences.

Double utterance exclusions:
- Do not report intentional repetition for lyrics, poems, literary repetition, onomatopoeia (e.g., しとしと, しくしく), or emphasis.
- Report only accidental duplicate utterances caused by conversion side effects (e.g., Governance ガバナンス -> ガバナンス ガバナンス).

TTS null rules:
- Null or missing audio/tts fields are normal recording-management state.
- Do not report null, empty, or missing audioPath, choiceAudioPaths, questionAudioPath, explanationAudioPath, audioUrl, choiceAudioUrls, questionAudioUrl, or explanationAudioUrl at any severity.
- Do not create low/info issues for missing audio or unrecorded units. recording-estimate handles recording state separately.

TTS fix suggestion rules:
- TTS suggestions are limited to pronunciation/readability changes: readings, kana/phonetic spelling, symbol readings, and language tags.
""" + language_tag_rules + """
- Closing tags such as [/en-US] or [/ja-JP] do not exist in Sokqa. Never suggest converting a switch tag into any [/...] closing tag.
- If the input already contains a [/...] closing tag, treat it as an invalid tag markup. Prefer the canonical switch-tag format or simple removal, and do not over-report minor tag cleanup.
- Treat the presence or absence of a trailing default-language return tag at the very end of a text item as a non-issue.
- For acronyms, abbreviations, symbols, or code-like terms that are likely to be misread, you may suggest a katakana reading (examples: GHQ -> ジーエイチキュー, PKO -> ピーケーオー, ODA -> オーディーエー, M&A -> エムアンドエー, CRM -> シーアールエム, ROI -> アールオーアイ, ROE -> アールオーイー, SEO -> エスイーオー, SEM -> エスイーエム, SLA -> エスエルエー, WBS -> ダブリュー・ビー・エス, PMBOK -> ピーエムボック).
- Do not force katakana readings for common full-spelled English words or general phrases unless pronunciation would be seriously wrong (examples: Cloud Computing, Machine Learning, Database, Marketing).
- Do not report reading or notation issues for Arabic numerals followed by common Japanese counters or units when standard cloud TTS can already read them correctly (examples: 1904年, 1945年, 1946年, 6月, 12日, 3時, 15分, 20秒, 500円, 80%, 10パーセント, 3人, 4回, 5個, 6件, 7番, 38度, 18歳).
- Treat Gregorian years and Japanese era years as non-issues when written in their normal numeric notation (examples: 1904年, 1945年, 1946年, 令和6年, 昭和20年). Keep the original notation and do not create reading or notation issues for them.
- Bare numbers without a unit are not excluded from review, because they may be model numbers, identifiers, or codes (example: A1904).
- In learner-facing text, 〜 and ◯◯ are the correct placeholder forms. Do not suggest square-bracket placeholders such as [名前], [場所], or [自分の名前], because square brackets are reserved for TTS language tags.
- Never change the original word, vocabulary, meaning, answer, quantity, proper noun, or technical term.
- Do not suggest paraphrases or semantic substitutions. For example, do not replace 有線LAN with LANケーブル.
- If a term needs a better spoken form, replace only that exact term with its reading (for example, 有線LAN -> ゆうせんラン), not with another word.
- Do not report reading issues for punctuation or decorative marks that TTS does not speak, such as 「」, 『』, (), （）, ・, commas, periods, or spacing. Report only the words inside those marks when the word itself has a real reading problem.
- Do not report a reading issue when the matching tts field already contains the suggested reading. For choices, check only the same choice index.
- Do not report suggestions whose only difference is the presence or absence of a trailing Japanese period "。". If content, reading, and meaning stay the same, suppress that issue.
- For tts_text_mismatch, suggestion は対象テキスト全体の「修正後の完全な形」を返すこと。部分差分・断片・途中で終わる文・省略形を出力してはならない。suggestion は original 全体を置き換える完全なテキストであること。
- For reading, double_utterance, and notation, excerpt must contain the exact source fragment to replace.
- For reading, double_utterance, and notation, suggestion must be the replacement text for that excerpt fragment only. Do not return the full unit sentence or paragraph.
- For tts.choiceTexts[index] issues, suggestion must be the replacement text for the excerpt inside that one choice index only. Do not return the full choice text or the full choiceTexts array unless the excerpt itself is the full choice text.
- If an exact replacement cannot be produced safely, do not create that issue.
""".strip()
        focus = "Inspect only audio/TTS quality. Do not report factual/style/leak display-text issues unless they directly affect TTS."
    else:
        category_block = """
Categories:
- factual: possible factual error or claim that needs human verification. Do not state it as certain; treat it as a suspicion.
- style: awkward style for learner-facing text, hearsay wording such as "ドキュメントによると" or "記載されています".
- leak: quiz explanation memo leakage, internal notes, prompt residue, placeholders, or authoring comments.
- For style only, limit suggestions to concise rewording of redundant phrasing or duplicated wording. Do not delete information content itself, including facts, causal relationships, impacts, conditions, or scope stated in the text. Removing redundant reference phrases such as "本文中で述べられている" and duplicated wording is allowed.

Target fields:
- document.documents[].text
- quiz.questions[].question
- quiz.questions[].choices[]
- quiz.questions[].explanation
- Ignore all tts fields, audio fields, and TTS-only text such as tts.text, tts.questionText, tts.choiceTexts, and tts.explanationText.

Notation rule:
- Report notation only when it is a display text quality issue by describing it under style/leak if appropriate. Spoken-reading notation belongs to the TTS quality check, not this check.
- In learner-facing text, 〜 and ◯◯ are the correct placeholder forms. Do not suggest square-bracket placeholders such as [名前], [場所], or [自分の名前], because square brackets are reserved for TTS language tags.
- Report only unresolved placeholders. Examples include stray 〜 in an unfinished sentence, fill-in placeholders such as ◯◯ or ○○ when they are not the intended finished exercise format, ASCII placeholder tokens such as XXX, square-bracket labels such as [Name], and generic labels such as Company Name.
- Do not report intended finished blanks such as ＿＿＿ or _____.
- Do not report valid 〜 usage such as 〜てください, numeric ranges like 10〜20, or notation used in math, chemistry, or grammar explanations.
- Treat completed fictional names or other fixed learner-facing expressions as non-issues when they are already finished text rather than unresolved placeholders.
""".strip()
        focus = "Inspect only source/display text quality. Do not report TTS pronunciation issues here."

    prompt = f"""
Return strict JSON only. Do not use markdown fences.

You are a quality check agent for a Sokqa learning pack. Inspect the generated doc/quiz JSON before audio recording.
Detect only clear issues. Do not report minor wording preferences.

{focus}

{category_block}

Severity:
- high: should be fixed before recording.
- medium: recommended to fix before release.
- low: minor but useful to review.

Rules:
- factual issues must use conservative confidence and wording such as "確認が必要".
- Write the issue and suggestion fields in Japanese. Keep category, severity, confidence, and location field names in the specified JSON schema.
- suggestion には修正後の本文のみを入れること。説明・注釈・理由・AIへの指示文・メタコメントを含めてはならない。
- suggestion は対象テキスト全体の「修正後の完全な形」を返すこと。部分差分・断片・途中で終わる文・省略形を出力してはならない。suggestion は original 全体を置き換える完全なテキストであること。
- Fill location.fileName with "{file_name}".
- Fill location.unitId with the document item id or quiz question id when available.
- Fill location.field with "text", "question", "choices", "explanation", or another concrete field.
- Return at most {max_issues} issues.
- If there are no clear issues, return an empty issues array.

Return this JSON shape:
{{
  "fileName": "{file_name}",
  "model": "model-name",
  "truncated": false,
  "issues": [
    {{
      "category": "style",
      "severity": "medium",
      "confidence": 0.8,
      "location": {{"fileName": "{file_name}", "unitId": "doc-1", "field": "text"}},
      "excerpt": "problematic excerpt",
      "issue": "brief issue description",
      "suggestion": "brief suggested fix"
    }}
  ]
}}

Source file JSON:
{source_json}
""".strip()
    return prompt, truncated


def _mock_quality_response(file_name: str, model: str, max_issues: int, *, mode: str = "text") -> QualityCheckResponse:
    samples = [
        {
            "category": "factual",
            "severity": "medium",
            "confidence": 0.55,
            "location": {"fileName": file_name, "unitId": "doc-1", "field": "text"},
            "excerpt": "市場シェアが必ず最大になります。",
            "issue": "断定的な事実主張で、確認が必要です。",
            "suggestion": "根拠がない場合は断定を避け、条件付きの表現にします。",
        },
        {
            "category": "reading",
            "severity": "medium",
            "confidence": 0.82,
            "location": {"fileName": file_name, "unitId": "doc-2", "field": "text"},
            "excerpt": "SQLとJSON",
            "issue": "略語がTTSで意図しない読みになる可能性があります。",
            "suggestion": "エスキューエルとジェイソン",
        },
        {
            "category": "double_utterance",
            "severity": "low",
            "confidence": 0.72,
            "location": {"fileName": file_name, "unitId": "doc-3", "field": "text"},
            "excerpt": "まず最初に、最初に確認します。",
            "issue": "同じ意味の語が重なって聞こえます。",
            "suggestion": "まず最初に確認します。",
        },
        {
            "category": "notation",
            "severity": "low",
            "confidence": 0.7,
            "location": {"fileName": file_name, "unitId": None, "field": "text"},
            "excerpt": "3C分析 / スリーシー分析",
            "issue": "同一概念の表記が揺れています。",
            "suggestion": "サンシー分析",
        },
        {
            "category": "style",
            "severity": "medium",
            "confidence": 0.85,
            "location": {"fileName": file_name, "unitId": "doc-4", "field": "text"},
            "excerpt": "ドキュメントによると、重要です。",
            "issue": "学習者向け本文として伝聞調が残っています。",
            "suggestion": "直接説明する文体に直します。",
        },
        {
            "category": "leak",
            "severity": "high",
            "confidence": 0.9,
            "location": {"fileName": file_name, "unitId": "q-1", "field": "explanation"},
            "excerpt": "TODO: あとで根拠を追加",
            "issue": "内部メモが解説に残っています。",
            "suggestion": "内部メモを削除し、学習者向けの解説に置き換えます。",
        },
    ]
    allowed = TEXT_QUALITY_CATEGORIES if mode == "text" else TTS_QUALITY_CATEGORIES
    filtered = [QualityIssue.model_validate(item) for item in samples if item["category"] in allowed]
    if mode == "tts":
        filtered.append(
            QualityIssue.model_validate(
                {
                    "category": "tts_text_mismatch",
                    "severity": "medium",
                    "confidence": 0.68,
                    "location": {"fileName": file_name, "unitId": "doc-5", "field": "tts.text"},
                    "excerpt": "meaning-changing fragment",
                    "issue": "tts.text が元テキストと意味的にずれている可能性があります。",
                    "suggestion": "meaning-preserving fragment",
                }
            )
        )
    return QualityCheckResponse(fileName=file_name, model=model, issues=filtered[:max_issues], truncated=len(filtered) > max_issues)
