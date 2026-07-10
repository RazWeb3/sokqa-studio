"""Language Learning 専用 Quality Checker 処理（Phase 6: Quality 移設）。

quality_checker.py から切り出した語学専用ロジックをここへ集約する。
learningLanguage / choiceLanguageMode に基づく検証を担当。
Standard 側は quality_checker.py に残り、Phase 6 時点は既存挙動を維持する。
"""

from typing import Any

from app.schemas.common import default_speech_language_code
from app.schemas.quality import QualityIssue
from app.services.language_detection import choice_set_language_state, language_script, leading_script


def _speech_code(language: str) -> str:
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


def deterministic_tts_issues(file_name: str, content: dict[str, Any]) -> list[QualityIssue]:
    """語学専用の決定論的 TTS/選択肢検証（quality_checker._deterministic_tts_issues の移設）。"""
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
