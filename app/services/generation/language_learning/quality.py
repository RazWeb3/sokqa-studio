"""Language Learning 専用 Quality Checker 処理（Phase 6: Quality 移設）。

quality_checker.py から切り出した語学専用ロジックをここへ集約する。
learningLanguage / choiceLanguageMode に基づく検証を担当。
Standard 側は quality_checker.py に残り、Phase 6 時点は既存挙動を維持する。
"""

from typing import Any

import re

from app.schemas.common import default_speech_language_code
from app.schemas.quality import QualityIssue
from app.services.language_detection import (
    choice_set_language_state,
    language_script,
    leading_script,
    scripts_in_text,
)


def _token_scripts(token: str) -> set[str]:
    # トークンに含まれるスクリプト集合（CJK/日本語とラテンを正しく区別）。
    return scripts_in_text(token)


def _contains_learning_language_text(text: str, learning_language: str) -> bool:
    """与えられた text に学習言語（learning_language）のフレーズが含まれるか。

    学習言語のスクリプト（例: 英語=latn、日本語=japanese/cjk）を含むトークンがあれば True。
    スクリプトが同じ場合（pack=en, learning=ja 等）は言語タグ付きセグメント
    （[en-US]...[/] 等）の有無でも判定する。
    """
    if not text or not learning_language:
        return False
    learn_script = language_script(str(learning_language))
    # ja/zh は scripts_in_text 上 "japanese" / "cjk" を区別するが、学習言語としては同一視。
    learn_scripts = {learn_script}
    if learn_script == "japanese":
        learn_scripts.add("cjk")
    if learn_script == "cjk":
        learn_scripts.add("japanese")
    for token in str(text).split():
        if not token:
            continue
        if _token_scripts(token) & learn_scripts:
            return True
    # 言語タグ付きセグメントの検出（スクリプトが同じ場合の補完）。
    if re.search(r"\[[a-zA-Z]{2}-[A-Z]{2}\]", str(text)):
        return True
    return False


def _document_has_tts(unit: dict[str, Any]) -> bool:
    tts = unit.get("tts")
    if not isinstance(tts, dict):
        return False
    return bool(tts.get("text") or tts.get("ttsText") or tts.get("utteranceText"))


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


def deterministic_ll_structure_issues(
    file_name: str, content: dict[str, Any], *, allow_language_tags: bool = False
) -> list[QualityIssue]:
    """語学教材固有の構造検証（Phase 10: ll_structure カテゴリ）。

    - ドキュメント: 学習言語含有（学習言語フレーズの存在）、TTS網羅（multilingual で学習言語
      フレーズを含むユニットは TTS 必須）。
    - クイズ: 問題が学習対象（フレーズ・表現・語彙・文型）に関連していること。章説明や教材
      メタ情報のみを問う問題（学習言語フレーズに全く接地していない問題）を検出する。
      応用・統合問題を弾かないよう、問題文へのフレーズの直接出現ではなく「学習言語テキストを
      含むか」で判定する。

    learningLanguage がないコンテンツは語学教材ではないため空を返す。
    """
    learning_language = content.get("learningLanguage")
    if not learning_language:
        return []
    issues: list[QualityIssue] = []
    content_type = content.get("type")
    if content_type == "document":
        for unit in content.get("documents") or []:
            if not isinstance(unit, dict):
                continue
            unit_id = str(unit.get("id") or "")
            text = str(unit.get("text") or "")
            if not _contains_learning_language_text(text, str(learning_language)):
                issues.append(
                    _quality_issue(
                        file_name,
                        unit_id,
                        "text",
                        unit.get("title") or text[:40],
                        "学習言語（" + str(learning_language) + "）のフレーズが含まれていません。語学学習教材として無効です。",
                        "導入の直後に学習言語のフレーズを提示してください。",
                        category="ll_structure",
                    )
                )
                continue
            if allow_language_tags and not _document_has_tts(unit):
                issues.append(
                    _quality_issue(
                        file_name,
                        unit_id,
                        "tts",
                        text[:40],
                        "学習言語フレーズを含むユニットですが TTS がありません。発音学習ができません。",
                        "multilingual モードでこのユニットの TTS を生成してください。",
                        category="ll_structure",
                    )
                )
    elif content_type == "quiz":
        for question in content.get("questions") or []:
            if not isinstance(question, dict):
                continue
            unit_id = str(question.get("id") or "")
            question_text = str(question.get("question") or "")
            explanation = str(question.get("explanation") or "")
            choices = [str(choice) for choice in question.get("choices") or []]
            grounded = _contains_learning_language_text(question_text, str(learning_language)) or any(
                _contains_learning_language_text(choice, str(learning_language)) for choice in choices
            )
            if not grounded and not _contains_learning_language_text(explanation, str(learning_language)):
                issues.append(
                    _quality_issue(
                        file_name,
                        unit_id,
                        "question",
                        question_text[:60],
                        "英語学習（" + str(learning_language) + "）の問題ですが、学習言語フレーズに全く接地していません（章メタ情報等のみの問題）。",
                        "学習対象のフレーズ・表現・語彙・文型に関連する問題に修正してください。",
                        category="ll_structure",
                    )
                )
    return issues
