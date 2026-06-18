from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.schemas.quality import QualityCheckResponse, QualityIssue
from app.schemas.request import TtsRecordingTarget
from app.services.gemini_client import GeminiClient
from app.services.tts_recording_api import load_target_pack


MAX_QUALITY_INPUT_CHARS = 30000
TEXT_QUALITY_CATEGORIES = {"factual", "style", "leak"}
TTS_QUALITY_CATEGORIES = {"reading", "double_utterance", "notation", "tts_text_mismatch"}
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

    if settings.gemini_provider == "mock":
        return _mock_quality_response(loaded.file.name, model, max_issues, mode=mode)

    prompt, input_truncated = _quality_prompt(loaded.file.name, loaded.file.content, max_issues, mode=mode)
    try:
        data = _generate_json_with_retry(lambda: GeminiClient().generate_json(prompt, model=model))
    except Exception as exc:
        raise QualityCheckError(f"quality check LLM call failed: {exc}") from exc

    try:
        return _quality_response_from_data(
            data,
            file_name=loaded.file.name,
            model=model,
            max_issues=max_issues,
            allowed_categories=TEXT_QUALITY_CATEGORIES if mode == "text" else TTS_QUALITY_CATEGORIES,
            suppress_tts_null_issues=mode == "tts",
            input_truncated=input_truncated,
            source_content=loaded.file.content if mode == "tts" else None,
        )
    except (TypeError, ValidationError, ValueError) as exc:
        raise QualityCheckError(f"quality check response validation failed: {exc}") from exc


def _generate_json_with_retry(
    generate: Callable[[], dict[str, Any]],
    *,
    attempts: int = 3,
    initial_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return generate()
        except Exception as exc:
            last_error = exc
            if attempt == attempts - 1:
                break
            sleep(initial_delay * (2**attempt))
    raise last_error or QualityCheckError("quality check LLM call failed")


def _quality_response_from_data(
    data: dict[str, Any],
    *,
    file_name: str,
    model: str,
    max_issues: int,
    allowed_categories: set[str] | None = None,
    suppress_tts_null_issues: bool = False,
    input_truncated: bool = False,
    source_content: dict[str, Any] | None = None,
) -> QualityCheckResponse:
    raw_issues = data.get("issues")
    if not isinstance(raw_issues, list):
        raise ValueError("response must contain an issues array")

    issues = [QualityIssue.model_validate(item) for item in raw_issues]
    if allowed_categories is not None:
        issues = [issue for issue in issues if issue.category in allowed_categories]
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
    truncated = bool(data.get("truncated")) or input_truncated or len(issues) > max_issues
    return QualityCheckResponse(
        fileName=str(data.get("fileName") or file_name),
        model=model,
        issues=issues[:max_issues],
        truncated=truncated,
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


def _tts_text_for_issue_location(content: dict[str, Any], issue: QualityIssue) -> str:
    unit = _find_quality_unit(content, issue.location.unitId)
    if not unit:
        return ""
    tts = unit.get("tts") or {}
    if not isinstance(tts, dict) or tts.get("ttsNeedsRefresh"):
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


def _quality_prompt(file_name: str, content: dict[str, Any], max_issues: int, *, mode: str) -> tuple[str, bool]:
    source_json = json.dumps(content, ensure_ascii=False, indent=2)
    truncated = len(source_json) > MAX_QUALITY_INPUT_CHARS
    if truncated:
        source_json = source_json[:MAX_QUALITY_INPUT_CHARS]

    if mode == "tts":
        category_block = """
Categories:
- reading: TTS misreading risks such as acronyms, code terms, symbols, or mixed-language spans.
- double_utterance: duplicated wording in tts fields that would be spoken twice or sounds redundant.
- notation: inconsistent spoken notation/readings. Do not report visual notation issues here.
- tts_text_mismatch: clear semantic mismatch between source text and tts text. Only report when meaning, answer, quantity, negation, or proper nouns clearly differ. Do not report kana conversion, reading correction, language tags, or punctuation differences.

TTS null rules:
- Null or missing audio/tts fields are normal recording-management state.
- Do not report null, empty, or missing audioPath, choiceAudioPaths, questionAudioPath, explanationAudioPath, audioUrl, choiceAudioUrls, questionAudioUrl, or explanationAudioUrl at any severity.
- Do not create low/info issues for missing audio or unrecorded units. recording-estimate handles recording state separately.

TTS fix suggestion rules:
- TTS suggestions are limited to pronunciation/readability changes: readings, kana/phonetic spelling, symbol readings, and language tags.
- Never change the original word, vocabulary, meaning, answer, quantity, proper noun, or technical term.
- Do not suggest paraphrases or semantic substitutions. For example, do not replace 有線LAN with LANケーブル.
- If a term needs a better spoken form, replace only that exact term with its reading (for example, 有線LAN -> ゆうせんラン), not with another word.
- Do not report reading issues for punctuation or decorative marks that TTS does not speak, such as 「」, 『』, (), （）, ・, commas, periods, or spacing. Report only the words inside those marks when the word itself has a real reading problem.
- Do not report a reading issue when the matching tts field already contains the suggested reading. For choices, check only the same choice index.
- For reading, double_utterance, notation, and tts_text_mismatch, excerpt must contain the exact source fragment to replace.
- suggestion must be the replacement text for that excerpt fragment only. Do not return the full unit sentence or paragraph.
- For tts.choiceTexts[index] issues, suggestion must be the replacement text for the excerpt inside that one choice index only. Do not return the full choice text or the full choiceTexts array unless the excerpt itself is the full choice text.
- If an exact replacement cannot be produced safely, keep suggestion as a concise explanation; the fix step may leave it unapplied.
""".strip()
        focus = "Inspect only audio/TTS quality. Do not report factual/style/leak display-text issues unless they directly affect TTS."
    else:
        category_block = """
Categories:
- factual: possible factual error or claim that needs human verification. Do not state it as certain; treat it as a suspicion.
- style: awkward style for learner-facing text, hearsay wording such as "ドキュメントによると" or "記載されています".
- leak: quiz explanation memo leakage, internal notes, prompt residue, placeholders, or authoring comments.

Notation rule:
- Report notation only when it is a display text quality issue by describing it under style/leak if appropriate. Spoken-reading notation belongs to the TTS quality check, not this check.
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
