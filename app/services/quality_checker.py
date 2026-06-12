from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.schemas.quality import QualityCheckResponse, QualityIssue
from app.schemas.request import TtsRecordingTarget
from app.services.gemini_client import GeminiClient
from app.services.tts_recording_api import load_target_pack


MAX_QUALITY_INPUT_CHARS = 30000


class QualityCheckError(RuntimeError):
    pass


def check_pack_quality(target: TtsRecordingTarget, max_issues: int = 50) -> QualityCheckResponse:
    loaded = load_target_pack(target)
    settings = get_settings()
    model = settings.quality_model

    if settings.gemini_provider == "mock":
        return _mock_quality_response(loaded.file.name, model, max_issues)

    prompt, input_truncated = _quality_prompt(loaded.file.name, loaded.file.content, max_issues)
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
            input_truncated=input_truncated,
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
    input_truncated: bool = False,
) -> QualityCheckResponse:
    raw_issues = data.get("issues")
    if not isinstance(raw_issues, list):
        raise ValueError("response must contain an issues array")

    issues = [QualityIssue.model_validate(item) for item in raw_issues]
    truncated = bool(data.get("truncated")) or input_truncated or len(issues) > max_issues
    return QualityCheckResponse(
        fileName=str(data.get("fileName") or file_name),
        model=model,
        issues=issues[:max_issues],
        truncated=truncated,
    )


def _quality_prompt(file_name: str, content: dict[str, Any], max_issues: int) -> tuple[str, bool]:
    source_json = json.dumps(content, ensure_ascii=False, indent=2)
    truncated = len(source_json) > MAX_QUALITY_INPUT_CHARS
    if truncated:
        source_json = source_json[:MAX_QUALITY_INPUT_CHARS]

    prompt = f"""
Return strict JSON only. Do not use markdown fences.

You are a quality check agent for a Sokqa learning pack. Inspect the generated doc/quiz JSON before audio recording.
Detect only clear issues. Do not report minor wording preferences.

Categories:
- factual: possible factual error or claim that needs human verification. Do not state it as certain; treat it as a suspicion.
- reading: text likely to be misread by TTS, such as acronyms, code terms, symbols, or mixed-language spans.
- double_utterance: duplicated wording that would be spoken twice or sounds redundant.
- notation: inconsistent notation within the same file, such as mixed spellings for the same concept.
- style: awkward style for learner-facing audio, hearsay wording such as "ドキュメントによると" or "記載されています".
- leak: quiz explanation memo leakage, internal notes, prompt residue, placeholders, or authoring comments.

Severity:
- high: should be fixed before recording.
- medium: recommended to fix before release.
- low: minor but useful to review.

Rules:
- factual issues must use conservative confidence and wording such as "確認が必要".
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


def _mock_quality_response(file_name: str, model: str, max_issues: int) -> QualityCheckResponse:
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
            "excerpt": "SQLとJSONを利用します。",
            "issue": "略語がTTSで意図しない読みになる可能性があります。",
            "suggestion": "必要ならTTS補正で読みを指定します。",
        },
        {
            "category": "double_utterance",
            "severity": "low",
            "confidence": 0.72,
            "location": {"fileName": file_name, "unitId": "doc-3", "field": "text"},
            "excerpt": "まず最初に、最初に確認します。",
            "issue": "同じ意味の語が重なって聞こえます。",
            "suggestion": "重複表現を1つに整理します。",
        },
        {
            "category": "notation",
            "severity": "low",
            "confidence": 0.7,
            "location": {"fileName": file_name, "unitId": None, "field": "text"},
            "excerpt": "3C分析 / スリーシー分析",
            "issue": "同一概念の表記が揺れています。",
            "suggestion": "文書内で表記を統一します。",
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
    return QualityCheckResponse(fileName=file_name, model=model, issues=samples[:max_issues], truncated=len(samples) > max_issues)
