import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.services.gemini_client import GeminiClient
from app.services.pack_paths import pack_root_prefix
from app.schemas.request import TtsRecordingTarget
from app.schemas.sokqa import GeneratedFile
from app.services.multilingual_detection import MultilingualStatus, detect_multilingual
from app.services import quality_checker
from app.services.generation.strategies.language_learning import LanguageLearningStrategy
from app.services.quality.context import QualityContext
from app.services.quality_checker import (
    TEXT_QUALITY_CATEGORIES,
    TTS_QUALITY_CATEGORIES,
    _build_text_replacement_suggestion,
    _generate_json_with_retry,
    _is_multilingual_invalid_tts_suggestion,
    _quality_prompt,
    _quality_response_from_data,
    _is_unresolved_placeholder_excerpt,
)
from app.schemas.quality import QualityIssue, QualityLocation
from main import app


client = TestClient(app)


def _write_document_pack(tmp_path: Path, monkeypatch) -> dict:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "public_base_url", "http://localhost:8000/generated")
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")

    creator_id = "creator_quality"
    content_id = "content_quality"
    version_id = "v20260612_120000"
    pack_name = "doc_01.json"
    prefix = pack_root_prefix(creator_id, content_id)
    target_dir = tmp_path / "generated" / prefix
    target_dir.mkdir(parents=True, exist_ok=True)
    file_version_id = "fv_20260612_120000_document_doc_01"
    content = {
        "id": "content_quality_doc_01",
        "type": "document",
        "schemaVersion": 1,
        "title": "品質チェック用ドキュメント",
        "language": "ja",
        "assetBaseUrl": f"http://localhost:8000/generated/{prefix}",
        "documents": [
            {"id": "doc-1", "text": "SQLとJSONを説明します。ドキュメントによると重要です。"}
        ],
    }
    (target_dir / "objects" / "doc").mkdir(parents=True, exist_ok=True)
    (target_dir / "versions" / version_id).mkdir(parents=True, exist_ok=True)
    (target_dir / "objects" / "doc" / f"{file_version_id}.json").write_text(
        json.dumps(content, ensure_ascii=False),
        encoding="utf-8",
    )
    manifest = {
        "id": "content_quality_manifest_r1",
        "type": "pack_manifest",
        "schemaVersion": 1,
        "contentId": content_id,
        "title": "品質チェック用ドキュメント",
        "creator": {"id": creator_id, "displayName": None},
        "revision": 1,
        "versionId": version_id,
        "buildId": "build_20260612_120000",
        "generatedAt": "2026-06-12T12:00:00+09:00",
        "change": {"operation": "initial_generate"},
        "items": [
            {
                "kind": "document",
                "name": pack_name,
                "title": "品質チェック用ドキュメント",
                "logicalId": "doc_01",
                "fileVersionId": file_version_id,
                "url": f"http://localhost:8000/generated/{prefix}/objects/doc/{file_version_id}.json",
            }
        ],
    }
    (target_dir / "versions" / version_id / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "creatorId": creator_id,
        "contentId": content_id,
        "versionId": version_id,
        "packName": pack_name,
        "kind": "document",
    }


def test_text_quality_check_mock_provider_returns_text_issues(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    response = client.post("/quality/text-check", json={"target": target})

    assert response.status_code == 200
    data = response.json()
    assert data["fileName"] == "doc_01.json"
    assert {issue["category"] for issue in data["issues"]} == {"factual", "style", "leak"}
    assert all(issue["location"]["fileName"] == "doc_01.json" for issue in data["issues"])


def test_text_quality_prompt_limits_targets_and_excludes_tts_fields() -> None:
    prompt, truncated = _quality_prompt(
        "sample_quiz.json",
        {
            "type": "quiz",
            "questions": [
                {
                    "id": "q-1",
                    "question": "問題文",
                    "choices": ["A", "B", "C", "D"],
                    "explanation": "解説",
                    "tts": {
                        "questionText": "TTS専用",
                        "choiceTexts": ["TTS A", "TTS B", "TTS C", "TTS D"],
                        "explanationText": "TTS解説",
                    },
                }
            ],
        },
        50,
        mode="text",
    )

    assert truncated is False
    assert "document.documents[].text" in prompt
    assert "quiz.questions[].question" in prompt
    assert "quiz.questions[].choices[]" in prompt
    assert "quiz.questions[].explanation" in prompt
    assert "Ignore all tts fields" in prompt
    assert "tts.questionText" in prompt


def test_text_quality_prompt_disallows_square_bracket_placeholder_suggestions() -> None:
    prompt, _ = _quality_prompt(
        "sample_quiz.json",
        {
            "type": "quiz",
            "questions": [
                {
                    "id": "q-1",
                    "question": "あなたの出身地を言ってください。",
                    "choices": ["東京", "大阪", "名古屋", "福岡"],
                    "explanation": "「私は〜出身です。」を使います。",
                }
            ],
        },
        50,
        mode="text",
    )

    assert "square-bracket placeholders such as [名前], [場所], or [自分の名前]" in prompt
    assert "Report only unresolved placeholders." in prompt
    assert "Do not report intended finished blanks such as ＿＿＿ or _____." in prompt
    assert "Do not report valid 〜 usage such as 〜てください, numeric ranges like 10〜20" in prompt
    assert "Treat completed fictional names or other fixed learner-facing expressions as non-issues" in prompt


def test_text_quality_prompt_keeps_style_suggestions_from_deleting_information() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-21", "text": "海外渡航制限と交流制限が影響しました。"}],
        },
        50,
        mode="text",
    )

    assert "For style only," not in prompt
    assert "limit suggestions to concise rewording of redundant phrasing or duplicated wording." in prompt
    assert "Do not delete information content itself, including facts, causal relationships, impacts, conditions, or scope stated in the text, for any of factual/style/leak categories." in prompt
    assert 'Removing redundant reference phrases such as "本文中で述べられている" and duplicated wording is allowed.' in prompt


def test_tts_quality_check_mock_provider_returns_tts_issues(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    response = client.post("/quality/tts-check", json={"target": target})

    assert response.status_code == 200
    data = response.json()
    assert {issue["category"] for issue in data["issues"]} == {
        "reading",
        "double_utterance",
        "notation",
        "tts_text_mismatch",
    }
    assert all(issue["severity"] != "high" for issue in data["issues"] if issue["category"] == "tts_text_mismatch")


def test_tts_quality_prompt_declares_switch_tags_and_forbids_closing_tags() -> None:
    prompt, _ = _quality_prompt(
        "sample_quiz.json",
        {
            "type": "quiz",
            "language": "ja",
            "questions": [
                {
                    "id": "q-1",
                    "question": "Good morning はどれですか。",
                    "choices": ["Good morning", "Hello", "Good evening", "Goodbye"],
                    "explanation": "朝の挨拶です。",
                    "tts": {"choiceTexts": ["[en-US]Good morning"]},
                }
            ],
        },
        50,
        mode="tts",
        multilingual=True,
    )

    assert "The canonical language-tag format is a switch tag sequence such as [en-US]English[ja-JP]." in prompt
    assert "Closing tags such as [/en-US] or [/ja-JP] do not exist in Sokqa." in prompt
    assert "Double utterance exclusions:" in prompt
    assert "Do not report intentional repetition for lyrics, poems, literary repetition, onomatopoeia" in prompt
    assert "Report only accidental duplicate utterances caused by conversion side effects" in prompt
    assert "square-bracket placeholders such as [名前], [場所], or [自分の名前]" in prompt


def test_tts_quality_prompt_for_normal_file_forbids_language_tags() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "本文", "tts": {"text": "本文"}}],
        },
        50,
        mode="tts",
        multilingual=False,
    )

    assert "This is a normal (non-multilingual) file. Never suggest adding language tags" in prompt
    assert "The canonical language-tag format is a switch tag sequence such as [en-US]English[ja-JP]." not in prompt
    assert "For acronyms, abbreviations, symbols, or code-like terms" in prompt
    assert "Do not force katakana readings for common full-spelled English words" in prompt


def test_tts_quality_prompt_excludes_years_and_number_with_units_from_reading_issues() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "1904年、1945年、1946年、令和6年、昭和20年。", "tts": {"text": "1904年、1945年、1946年、令和6年、昭和20年。"}}],
        },
        50,
        mode="tts",
        multilingual=False,
    )

    assert "Do not report reading or notation issues for Arabic numerals followed by common Japanese counters or units" in prompt
    assert "examples: 1904年, 1945年, 1946年, 6月, 12日, 3時, 15分, 20秒, 500円, 80%, 10パーセント, 3人, 4回, 5個, 6件, 7番, 38度, 18歳" in prompt
    assert "Treat Gregorian years and Japanese era years as non-issues when written in their normal numeric notation" in prompt
    assert "examples: 1904年, 1945年, 1946年, 令和6年, 昭和20年" in prompt


def test_tts_quality_prompt_keeps_acronyms_and_bare_numbers_reviewable() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "GHQ、PKO、ODA、A1904。", "tts": {"text": "GHQ、PKO、ODA、A1904。"}}],
        },
        50,
        mode="tts",
        multilingual=False,
    )

    assert "examples: GHQ -> ジーエイチキュー, PKO -> ピーケーオー, ODA -> オーディーエー, M&A -> エムアンドエー, CRM -> シーアールエム" in prompt
    assert "Bare numbers without a unit are not excluded from review" in prompt
    assert "example: A1904" in prompt


def test_tts_quality_prompt_multilingual_excludes_katakana_allowance_block() -> None:
    prompt, _ = _quality_prompt(
        "sample_quiz.json",
        {
            "type": "quiz",
            "language": "ja",
            "learningLanguage": "en",
            "questions": [
                {
                    "id": "q-1",
                    "question": "Good morning はどれですか。",
                    "choices": ["Good morning", "Hello", "Good evening", "Goodbye"],
                    "explanation": "朝の挨拶です。",
                    "tts": {"choiceTexts": ["[en-US]Good morning"]},
                }
            ],
        },
        50,
        mode="tts",
        multilingual=True,
    )

    # 多言語: カタカナ化許容ブロック(GHQ/PKO/ODA 等の例示)を含めてはならない。
    assert "examples: GHQ -> ジーエイチキュー, PKO -> ピーケーオー, ODA -> オーディーエー" not in prompt
    assert "The pack is multilingual." in prompt
    assert "Please -> プリーズ" in prompt
    assert "TPO -> ティーピーオー" in prompt
    # 閉じタグ禁止指示は multilingual でも残る。
    assert "Closing tags such as [/en-US] or [/ja-JP] do not exist in Sokqa." in prompt


def test_tts_quality_prompt_normal_includes_katakana_allowance_block() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "GHQ、PKO、ODA。", "tts": {"text": "GHQ、PKO、ODA。"}}],
        },
        50,
        mode="tts",
        multilingual=False,
    )

    # 単一言語: 既存のカタカナ化許容ブロックを維持する。
    assert "examples: GHQ -> ジーエイチキュー, PKO -> ピーケーオー, ODA -> オーディーエー" in prompt


def test_quality_prompt_requires_suggestion_to_be_finished_text_only() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "本文です。"}],
        },
        50,
        mode="tts",
    )

    assert "suggestion には修正後の本文のみを入れること。説明・注釈・理由・AIへの指示文・メタコメントを含めてはならない。" in prompt
    assert "If an exact replacement cannot be produced safely, do not create that issue." in prompt
    assert "keep suggestion as a concise explanation" not in prompt


def test_tts_quality_prompt_disallows_trailing_japanese_period_only_suggestions() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "本文です。", "tts": {"text": "本文です。"}}],
        },
        50,
        mode="tts",
    )

    assert "Do not report suggestions whose only difference is the presence or absence of a trailing Japanese period" in prompt


def test_quality_prompt_requires_replacement_fragment_for_text_categories() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "本文です。"}],
        },
        50,
        mode="text",
    )

    # 全文 suggestion ではなく、置換断片を要求する。
    assert "replaceFrom" in prompt
    assert "replaceTo" in prompt
    # 旧 FULL_REPLACE の全文生成指示は入らない。
    assert 'suggestion は対象テキスト全体の「修正後の完全な形」を返すこと。' not in prompt
    assert "部分差分・断片・途中で終わる文・省略形を出力してはならない。" not in prompt
    assert "差分断片" in prompt


def test_quality_prompt_requires_minimal_fix_and_original_preservation_for_text_categories() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "本文です。"}],
        },
        50,
        mode="text",
    )

    # 旧 preservation 文言が強化された後も保持される。
    assert "該当する箇所のみ" in prompt or "該当箇所のみ" in prompt
    assert "絶対に削除・要約・書き換えてはならない" in prompt
    assert "issue を立てないこと" in prompt


def test_quality_prompt_text_mode_includes_replacement_rule_and_excludes_tts() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "本文です。"}],
        },
        50,
        mode="text",
    )

    assert "replaceFrom" in prompt
    assert "replaceTo" in prompt
    assert "original 内に存在する" in prompt
    assert "1回だけ出現する曖昧さのない短い断片" in prompt

    # replacement_rule must not leak into tts mode.
    tts_prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "本文です。", "tts": {"text": "本文です。"}}],
        },
        50,
        mode="tts",
    )
    assert "replaceFrom" not in tts_prompt
    assert "replaceTo" not in tts_prompt
    assert "original 内に存在する" not in tts_prompt


def test_quality_prompt_text_mode_adds_factual_and_leak_assertion_guard() -> None:
    prompt, _ = _quality_prompt(
        "sample_doc.json",
        {
            "type": "document",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "本文です。"}],
        },
        50,
        mode="text",
    )

    assert "For factual, target only clear and confident factual errors." in prompt
    assert "Do not rewrite assertively items whose naming or criteria depend on school/sect/custom" in prompt
    assert "fix only the relevant fragment and never delete, summarize, or replace surrounding sentences." in prompt
    assert "For leak, replace only the author-facing comment / meta-expression fragment with learner-facing wording" in prompt
    assert "keep surrounding sentences and information as the original text." in prompt


def test_detect_multilingual_prioritizes_metadata_over_tags_and_structure() -> None:
    assert (
        detect_multilingual(
            {
                "type": "document",
                "metadata": {"multilingual": True},
                "learningLanguage": "en",
                "documents": [{"id": "doc-1", "text": "本文", "tts": {"text": "[en-US]Hello"}}],
            }
        )
        == MultilingualStatus.MULTILINGUAL
    )
    assert (
        detect_multilingual(
            {
                "type": "document",
                "metadata": {"multilingual": False},
                "learningLanguage": "en",
                "documents": [{"id": "doc-1", "text": "本文", "tts": {"text": "[en-US]Hello"}}],
            }
        )
        == MultilingualStatus.NORMAL
    )


def test_detect_multilingual_detects_existing_tts_language_tags() -> None:
    assert (
        detect_multilingual(
            {
                "type": "quiz",
                "questions": [
                    {
                        "id": "q-1",
                        "question": "Q",
                        "choices": ["A", "B", "C", "D"],
                        "answerIndex": 0,
                        "explanation": "E",
                        "tts": {"choiceTexts": ["[en-US]Good morning", "B", "C", "D"]},
                    }
                ],
            }
        )
        == MultilingualStatus.MULTILINGUAL
    )


def test_detect_multilingual_detects_multilingual_structure() -> None:
    assert (
        detect_multilingual(
            {
                "type": "quiz",
                "language": "ja",
                "learningLanguage": "en",
                "questions": [
                    {
                        "id": "q-1",
                        "question": "Q",
                        "choices": ["A", "B", "C", "D"],
                        "answerIndex": 0,
                        "explanation": "E",
                    }
                ],
            }
        )
        == MultilingualStatus.MULTILINGUAL
    )


def test_detect_multilingual_returns_unknown_when_undetectable() -> None:
    assert (
        detect_multilingual(
            {
                "type": "document",
                "language": "ja",
                "documents": [{"id": "doc-1", "text": "本文"}],
            }
        )
        == MultilingualStatus.UNKNOWN
    )


def test_quality_checker_uses_resolved_context_for_prompt(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    file = GeneratedFile(
        name="doc_unknown.json",
        kind="document",
        content={
            "id": "doc_unknown",
            "type": "document",
            "schemaVersion": 1,
            "title": "unknown",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "本文"}],
        },
    )
    monkeypatch.setattr(
        quality_checker,
        "load_target_pack",
        lambda _target: SimpleNamespace(file=file),
    )
    monkeypatch.setattr(quality_checker, "_quality_context_for_target", lambda *_args: QualityContext(LanguageLearningStrategy()))

    observed: dict[str, bool] = {}
    original_quality_prompt = quality_checker._quality_prompt

    def wrapped_quality_prompt(*args, **kwargs):
        observed["multilingual"] = bool(kwargs.get("multilingual"))
        return original_quality_prompt(*args, **kwargs)

    monkeypatch.setattr(quality_checker, "_quality_prompt", wrapped_quality_prompt)

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {"fileName": file.name, "model": "fake", "truncated": False, "issues": []}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = quality_checker.check_tts_quality(
        target=TtsRecordingTarget(packName=file.name, kind=file.kind),
        max_issues=50,
    )
    assert response.fileName == file.name
    assert observed["multilingual"] is True


def test_quality_checker_filters_empty_suggestion_issues_and_keeps_others() -> None:
    response = _quality_response_from_data(
        {
            "fileName": "sample.json",
            "model": "fake",
            "truncated": False,
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "sample.json", "unitId": "doc-1", "field": "tts.text"},
                    "excerpt": "SQL",
                    "issue": "略語が誤読されます。",
                    "suggestion": "",
                },
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "sample.json", "unitId": "doc-1", "field": "tts.text"},
                    "excerpt": "JSON",
                    "issue": "略語が誤読されます。",
                    "suggestion": "ジェイソン",
                },
            ],
        },
        file_name="sample.json",
        model="fake",
        max_issues=50,
    )

    assert [issue.excerpt for issue in response.issues] == ["JSON"]


def test_tts_quality_check_detects_missing_learning_language_choice_texts(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="quiz_english.json",
        kind="quiz",
        content={
            "id": "quiz_english",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "英会話",
            "language": "ja",
            "learningLanguage": "en",
            "choiceLanguageMode": "learning",
            "questions": [
                {
                    "id": "q-1",
                    "question": "朝の挨拶はどれですか。",
                    "choices": ["Good morning", "Hello", "Good evening", "Goodbye"],
                    "answerIndex": 0,
                    "explanation": "朝は Good morning を使います。",
                }
            ],
        },
    )
    monkeypatch.setattr(
        quality_checker,
        "load_target_pack",
        lambda _target: SimpleNamespace(file=file),
    )
    monkeypatch.setattr(quality_checker, "_quality_context_for_target", lambda *_args: QualityContext(LanguageLearningStrategy()))

    response = client.post(
        "/quality/tts-check",
        json={
            "target": {
                "creatorId": "creator",
                "contentId": "content",
                "versionId": "version",
                "packName": "quiz_english.json",
                "kind": "quiz",
            }
        },
    )

    assert response.status_code == 200
    issues = response.json()["issues"]
    missing = [issue for issue in issues if "choiceTexts" in issue["issue"]]
    assert len(missing) == 4
    assert [issue["location"]["field"] for issue in missing] == [
        "choices[0]",
        "choices[1]",
        "choices[2]",
        "choices[3]",
    ]


def test_tts_quality_check_detects_choice_language_mode_violation_and_mixed_choices(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="quiz_mixed_choices.json",
        kind="quiz",
        content={
            "id": "quiz_mixed_choices",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "英会話",
            "language": "ja",
            "learningLanguage": "en",
            "choiceLanguageMode": "learning",
            "questions": [
                {
                    "id": "q-1",
                    "question": "朝の挨拶はどれですか。",
                    "choices": ["Good morning", "こんにちは", "Good evening", "Goodbye"],
                    "answerIndex": 0,
                    "explanation": "朝は Good morning を使います。",
                }
            ],
        },
    )
    monkeypatch.setattr(
        quality_checker,
        "load_target_pack",
        lambda _target: SimpleNamespace(file=file),
    )
    monkeypatch.setattr(quality_checker, "_quality_context_for_target", lambda *_args: QualityContext(LanguageLearningStrategy()))

    response = client.post(
        "/quality/tts-check",
        json={
            "target": {
                "creatorId": "creator",
                "contentId": "content",
                "versionId": "version",
                "packName": "quiz_mixed_choices.json",
                "kind": "quiz",
            }
        },
    )

    assert response.status_code == 200
    issues = response.json()["issues"]
    assert any("学習言語ですが" in issue["issue"] for issue in issues)
    assert any("パック言語になっています" in issue["issue"] for issue in issues)


def test_tts_quality_check_detects_choice_text_length_mismatch(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="quiz_choice_text_mismatch.json",
        kind="quiz",
        content={
            "id": "quiz_choice_text_mismatch",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "英会話",
            "language": "ja",
            "learningLanguage": "en",
            "choiceLanguageMode": "learning",
            "questions": [
                {
                    "id": "q-1",
                    "question": "朝の挨拶はどれですか。",
                    "choices": ["Good morning", "Hello", "Good evening", "Goodbye"],
                    "answerIndex": 0,
                    "explanation": "朝は Good morning を使います。",
                    "tts": {"choiceTexts": ["[en-US]Good morning"]},
                }
            ],
        },
    )
    monkeypatch.setattr(
        quality_checker,
        "load_target_pack",
        lambda _target: SimpleNamespace(file=file),
    )
    monkeypatch.setattr(quality_checker, "_quality_context_for_target", lambda *_args: QualityContext(LanguageLearningStrategy()))

    response = client.post(
        "/quality/tts-check",
        json={
            "target": {
                "creatorId": "creator",
                "contentId": "content",
                "versionId": "version",
                "packName": "quiz_choice_text_mismatch.json",
                "kind": "quiz",
            }
        },
    )

    assert response.status_code == 200
    issues = response.json()["issues"]
    assert any("配列長" in issue["issue"] for issue in issues)


def test_tts_quality_location_is_normalized() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.9,
                    "location": {
                        "fileName": "quiz.json",
                        "unitId": "q-1",
                        "field": "tts.explanationText",
                    },
                    "excerpt": "SQL",
                    "issue": "読みを確認します。",
                    "suggestion": "エスキューエル",
                }
            ]
        },
        file_name="quiz.json",
        model="test",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
    )

    assert response.issues[0].location.field == "explanation"


def test_tts_quality_check_filters_null_audio_issues(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs: object) -> dict:
        return {
            "issues": [
                {
                    "category": "reading",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "audioPath"},
                    "excerpt": "audioPath is null",
                    "issue": "audioPath が null です。",
                    "suggestion": "録音してください。",
                },
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "SQL",
                    "issue": "SQL が誤読される可能性があります。",
                    "suggestion": "エスキューエルにします。",
                },
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/tts-check", json={"target": target})

    assert response.status_code == 200
    data = response.json()
    assert [issue["excerpt"] for issue in data["issues"]] == ["SQL"]


def test_tts_quality_check_accepts_top_level_issue_array(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs: object) -> list:
        return [
            {
                "category": "reading",
                "severity": "medium",
                "confidence": 0.8,
                "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                "excerpt": "SQL",
                "issue": "SQL が誤読される可能性があります。",
                "suggestion": "エスキューエルにします。",
            }
        ]

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/tts-check", json={"target": target})

    assert response.status_code == 200
    data = response.json()
    assert [issue["excerpt"] for issue in data["issues"]] == ["SQL"]
    assert data["truncated"] is False


def test_tts_quality_check_wraps_unexpected_errors_as_502(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def raise_attribute_error(*args, **kwargs):
        raise AttributeError("boom")

    monkeypatch.setattr(GeminiClient, "generate_json", lambda *_args, **_kwargs: {"issues": []})
    monkeypatch.setattr(quality_checker, "_quality_response_from_data", raise_attribute_error)

    response = client.post("/quality/tts-check", json={"target": target})

    assert response.status_code == 502
    assert "unexpected" in response.json()["detail"]


def test_tts_quality_response_filters_unspoken_symbol_only_reading_issues() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "low",
                    "confidence": 0.7,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "question"},
                    "excerpt": "「」",
                    "issue": "かぎ括弧の読み方に関する指摘です。",
                    "suggestion": "読まない",
                },
                {
                    "category": "reading",
                    "severity": "low",
                    "confidence": 0.7,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "choices"},
                    "excerpt": "・",
                    "issue": "中黒の読み方に関する指摘です。",
                    "suggestion": "読まない",
                },
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "question"},
                    "excerpt": "SQL",
                    "issue": "括弧内の専門語が誤読される可能性があります。",
                    "suggestion": "エスキューエル",
                },
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
    )

    assert [issue.excerpt for issue in response.issues] == ["SQL"]


def test_tts_quality_response_keeps_words_inside_brackets() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "『API』",
                    "issue": "括弧内のAPIが誤読される可能性があります。",
                    "suggestion": "『エーピーアイ』",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
    )

    assert [issue.excerpt for issue in response.issues] == ["『API』"]


def test_tts_quality_response_does_not_filter_non_reading_categories() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "tts_text_mismatch",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "tts.text"},
                    "excerpt": "・",
                    "issue": "tts.text が元テキストと意味的にずれている可能性があります。",
                    "suggestion": "意味を一致させる",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
    )

    assert [issue.category for issue in response.issues] == ["tts_text_mismatch"]


def test_quality_response_filters_same_excerpt_and_suggestion_after_normalization() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "短い フレーズ",
                    "issue": "同じ内容を繰り返しています。",
                    "suggestion": "短いフレーズ",
                }
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
    )

    assert response.issues == []


def test_quality_response_rejects_fragment_suggestion_with_trailing_comma(caplog) -> None:
    caplog.set_level("INFO")

    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_09.json", "unitId": "doc-9", "field": "text"},
                    "excerpt": "ストッキングは着用が推奨される場合が多いです。",
                    "issue": "文が不自然です。",
                    "suggestion": "ストッキングは着用が推奨される場合が多く、",
                }
            ]
        },
        file_name="doc_09.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
    )

    assert response.issues == []
    assert "quality_check.rejected_fragment_suggestions count=1 file=doc_09.json" in caplog.text


def test_quality_response_keeps_finished_sentences_that_end_with_japanese_period() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "概要を説明します",
                    "issue": "文末を整えます。",
                    "suggestion": "概要について。",
                },
                {
                    "category": "factual",
                    "severity": "medium",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-2", "field": "text"},
                    "excerpt": "条件を説明します",
                    "issue": "断定を避けます。",
                    "suggestion": "条件があるので。",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
    )

    assert [issue.suggestion for issue in response.issues] == ["概要について。", "条件があるので。"]


def test_quality_response_does_not_apply_fragment_rule_to_word_level_tts_categories() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "choices[0]"},
                    "excerpt": "M&A",
                    "issue": "読みを修正します。",
                    "suggestion": "エムアンドエー",
                },
                {
                    "category": "notation",
                    "severity": "low",
                    "confidence": 0.7,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "choices[1]"},
                    "excerpt": "◯◯",
                    "issue": "表記を修正します。",
                    "suggestion": "まるまる",
                },
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
    )

    assert [issue.suggestion for issue in response.issues] == ["エムアンドエー", "まるまる"]


def test_quality_response_filters_obvious_meta_annotation_suggestion_and_keeps_normal_text() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-42", "field": "text"},
                    "excerpt": "TTS",
                    "issue": "本文に補足が必要です。",
                    "suggestion": "TTSが自然に読み上げられるよう、具体的な指示が必要です。",
                },
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-42", "field": "text"},
                    "excerpt": "確認します",
                    "issue": "本文を自然な言い回しにします。",
                    "suggestion": "まず確認します。",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
    )

    assert [issue.suggestion for issue in response.issues] == ["まず確認します。"]


def test_quality_response_keeps_unresolved_placeholder_issues() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "leak",
                    "severity": "high",
                    "confidence": 0.9,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "◯◯",
                    "issue": "未確定プレースホルダーが残っています。",
                    "suggestion": "株式会社さくらソフト",
                },
                {
                    "category": "leak",
                    "severity": "high",
                    "confidence": 0.9,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "[Name]",
                    "issue": "未確定プレースホルダーが残っています。",
                    "suggestion": "田中さん",
                },
                {
                    "category": "leak",
                    "severity": "high",
                    "confidence": 0.9,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "Company Name",
                    "issue": "未確定プレースホルダーが残っています。",
                    "suggestion": "Sakura Learning",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
    )

    assert [issue.excerpt for issue in response.issues] == ["◯◯", "[Name]", "Company Name"]


def test_quality_response_filters_allowed_placeholder_forms_and_valid_tilde_usage() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "leak",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "＿＿＿",
                    "issue": "未確定プレースホルダーが残っています。",
                    "suggestion": "答えを書いてください。",
                },
                {
                    "category": "leak",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "_____",
                    "issue": "未確定プレースホルダーが残っています。",
                    "suggestion": "answer",
                },
                {
                    "category": "style",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "〜てください",
                    "issue": "プレースホルダー記号が残っています。",
                    "suggestion": "説明してください",
                },
                {
                    "category": "style",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "10〜20",
                    "issue": "プレースホルダー記号が残っています。",
                    "suggestion": "10から20",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
    )

    assert response.issues == []


def test_quality_response_filters_completed_fixed_expression_placeholder_false_positive() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "leak",
                    "severity": "medium",
                    "confidence": 0.7,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "さくらソフト株式会社",
                    "issue": "プレースホルダーのまま残っています。",
                    "suggestion": "別の社名に直します。",
                }
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
    )

    assert response.issues == []


def test_quality_response_filters_suggestion_same_as_source_field_after_normalization() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-40", "field": "text"},
                    "excerpt": "短い フレーズ",
                    "issue": "同じ内容を繰り返しています。",
                    "suggestion": "短いフレーズ",
                }
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        source_content={
            "type": "document",
            "documents": [{"id": "doc-40", "text": "短い フレーズ"}],
        },
    )

    assert response.issues == []


def test_quality_response_filters_same_source_and_suggestion_with_nfkc_whitespace_normalization(caplog) -> None:
    caplog.set_level("INFO")

    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_38.json", "unitId": "doc-38", "field": "text"},
                    "excerpt": "ＡＢＣ",
                    "issue": "同じ内容です。",
                    "suggestion": " A B C \n",
                }
            ]
        },
        file_name="doc_38.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content={
            "type": "document",
            "documents": [{"id": "doc-38", "text": "ＡＢＣ"}],
        },
    )

    assert response.issues == []
    assert "quality_check.rejected_same_as_original_suggestions count=1 file=doc_38.json" in caplog.text


def test_quality_response_keeps_punctuation_only_fix() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "短いフレーズ",
                    "issue": "句読点を補います。",
                    "suggestion": "短いフレーズ。",
                }
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content={
            "type": "document",
            "documents": [{"id": "doc-1", "text": "短いフレーズ"}],
        },
    )

    assert [issue.suggestion for issue in response.issues] == ["短いフレーズ。"]


def test_quality_response_filters_trailing_japanese_period_only_suggestion(caplog) -> None:
    caplog.set_level("INFO")

    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "tts_text_mismatch",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "quiz_02.json", "unitId": "q-2", "field": "choices[0]"},
                    "excerpt": "メモした内容をすぐに削除しないこと。",
                    "issue": "末尾句点を削除すると自然です。",
                    "suggestion": "メモした内容をすぐに削除しないこと",
                }
            ]
        },
        file_name="quiz_02.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content={
            "type": "quiz",
            "questions": [
                {
                    "id": "q-2",
                    "question": "該当するものを選んでください。",
                    "choices": ["メモした内容をすぐに削除しないこと。", "B", "C", "D"],
                    "answerIndex": 0,
                    "explanation": "解説",
                    "tts": {"choiceTexts": ["メモした内容をすぐに削除しないこと。", "B", "C", "D"]},
                }
            ],
        },
    )

    assert response.issues == []
    assert "reason=trailing_period_only" in caplog.text


def test_quality_response_keeps_suggestion_when_text_changes_beyond_trailing_period() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "tts_text_mismatch",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_02.json", "unitId": "q-2", "field": "choices[0]"},
                    "excerpt": "メモした内容をすぐに削除しないこと。",
                    "issue": "内容も読みも変わります。",
                    "suggestion": "メモした内容はすぐに削除しないこと",
                }
            ]
        },
        file_name="quiz_02.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content={
            "type": "quiz",
            "questions": [
                {
                    "id": "q-2",
                    "question": "該当するものを選んでください。",
                    "choices": ["メモした内容をすぐに削除しないこと。", "B", "C", "D"],
                    "answerIndex": 0,
                    "explanation": "解説",
                    "tts": {"choiceTexts": ["メモした内容をすぐに削除しないこと。", "B", "C", "D"]},
                }
            ],
        },
    )

    assert [issue.suggestion for issue in response.issues] == ["メモした内容はすぐに削除しないこと"]


@pytest.mark.parametrize(
    "text",
    [
        "〇〇株式会社",
        "△△様",
        "□□部長",
        "株式会社〇〇",
        "××商事",
        "◯◯大学",
    ],
)
def test_unresolved_placeholder_detects_masked_names(text: str) -> None:
    assert _is_unresolved_placeholder_excerpt(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "＿＿＿",
        "10〜20",
        "〜てください",
        "〇か×で答える",
        "三角形の性質",
        "トヨタ自動車",
    ],
)
def test_unresolved_placeholder_ignores_valid_text(text: str) -> None:
    assert _is_unresolved_placeholder_excerpt(text) is False


def test_quality_response_keeps_mid_sentence_punctuation_change() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "low",
                    "confidence": 0.7,
                    "location": {"fileName": "doc_03.json", "unitId": "doc-3", "field": "text"},
                    "excerpt": "重要な点を確認し、共有します。",
                    "issue": "読点位置を調整します。",
                    "suggestion": "重要な点を確認し共有します。",
                }
            ]
        },
        file_name="doc_03.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content={
            "type": "document",
            "documents": [{"id": "doc-3", "text": "重要な点を確認し、共有します。"}],
        },
    )

    assert [issue.suggestion for issue in response.issues] == ["重要な点を確認し共有します。"]


def test_tts_quality_response_filters_same_source_and_suggestion_with_nfkc_whitespace_normalization() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "tts_text_mismatch",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-1", "field": "question"},
                    "excerpt": "Ａ Ｂ Ｃ",
                    "issue": "元テキストと同じです。",
                    "suggestion": "ABC",
                }
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content={
            "type": "quiz",
            "questions": [
                {
                    "id": "q-1",
                    "question": "Ａ Ｂ Ｃ",
                    "choices": ["1", "2", "3", "4"],
                    "answerIndex": 0,
                    "explanation": "exp",
                    "tts": {"questionText": "Ａ Ｂ Ｃ"},
                }
            ],
        },
    )

    assert response.issues == []


def test_quality_response_filters_duplicate_location_and_excerpt() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "low",
                    "confidence": 0.6,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-5", "field": "text"},
                    "excerpt": "短いフレーズ",
                    "issue": "少し冗長です。",
                    "suggestion": "短い文にします。",
                },
                {
                    "category": "style",
                    "severity": "low",
                    "confidence": 0.55,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-5", "field": "text"},
                    "excerpt": "短いフレーズ",
                    "issue": "ほぼ同じ指摘です。",
                    "suggestion": "表現を簡潔にします。",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
    )

    assert len(response.issues) == 1
    assert response.issues[0].issue == "少し冗長です。"


def test_tts_quality_response_filters_already_corrected_choice_reading_issue() -> None:
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-14",
                "question": "IT部門の役割はどれですか?",
                "choices": ["IT部門", "営業部門", "経理部門", "総務部門"],
                "answerIndex": 0,
                "explanation": "IT部門は情報システムを支えます。",
                "tts": {"choiceTexts": ["アイティー部門", "営業部門", "経理部門", "総務部門"]},
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-14", "field": "choices[0]"},
                    "excerpt": "IT",
                    "issue": "IT はアイティーと読む必要があります。",
                    "suggestion": "アイティー",
                },
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert response.issues == []


def test_tts_quality_response_keeps_uncorrected_choice_reading_issue() -> None:
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-14",
                "question": "IT部門の役割はどれですか?",
                "choices": ["IT部門", "OS管理", "営業部門", "総務部門"],
                "answerIndex": 0,
                "explanation": "IT部門は情報システムを支えます。",
                "tts": {"choiceTexts": ["アイティー部門", "OS管理", "営業部門", "総務部門"]},
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-14", "field": "choices[0]"},
                    "excerpt": "IT",
                    "issue": "IT はアイティーと読む必要があります。",
                    "suggestion": "アイティー",
                },
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-14", "field": "choices[1]"},
                    "excerpt": "OS",
                    "issue": "OS はオーエスと読む必要があります。",
                    "suggestion": "オーエス",
                },
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert [issue.excerpt for issue in response.issues] == ["OS"]


def test_tts_quality_response_does_not_filter_reading_by_other_choice_index() -> None:
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-14",
                "question": "IT部門の役割はどれですか?",
                "choices": ["IT部門", "OS管理", "営業部門", "総務部門"],
                "answerIndex": 0,
                "explanation": "IT部門は情報システムを支えます。",
                "tts": {"choiceTexts": ["アイティー部門", "オーエス管理", "営業部門", "総務部門"]},
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_01.json", "unitId": "q-14", "field": "choices[0]"},
                    "excerpt": "IT",
                    "issue": "IT はオーエスと読む必要があります。",
                    "suggestion": "オーエス",
                },
            ]
        },
        file_name="quiz_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert [issue.excerpt for issue in response.issues] == ["IT"]


def test_tts_quality_response_does_not_filter_non_reading_already_corrected_issue() -> None:
    content = {
        "type": "document",
        "documents": [
            {"id": "doc-1", "text": "ITを説明します。", "tts": {"text": "アイティーを説明します。"}}
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "tts_text_mismatch",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "tts.text"},
                    "excerpt": "IT",
                    "issue": "意味のずれを確認してください。",
                    "suggestion": "アイティー",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert [issue.category for issue in response.issues] == ["tts_text_mismatch"]


def test_text_quality_response_attaches_original_unit_full_text() -> None:
    doc_content = {
        "type": "document",
        "documents": [
            {"id": "doc-2", "text": "クラウドは重要になります。応用範囲が広がります。", "tts": {"text": "クラウドはじゅうようになります。"}},
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_02.json", "unitId": "doc-2", "field": "text"},
                    "excerpt": "重要になります",
                    "issue": "断定的な表現です。",
                    "suggestion": "重要になる可能性があります",
                },
            ]
        },
        file_name="doc_02.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=doc_content,
    )

    assert len(response.issues) == 1
    issue = response.issues[0]
    assert issue.original == doc_content["documents"][0]["text"]
    assert issue.excerpt == "重要になります"


def test_text_quality_response_attaches_original_for_quiz_explanation_and_choices() -> None:
    quiz_content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-5",
                "question": "クラウドの特徴として正しいものはどれですか。",
                "choices": ["拡張性が高い", "拡張性が低い", "費用が高い", "利用が難しい"],
                "answerIndex": 0,
                "explanation": "クラウドは必要な時に必要な分だけ利用できます。拡張性が高いのが特徴です。",
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_05.json", "unitId": "q-5", "field": "explanation"},
                    "excerpt": "拡張性が高いのが特徴です。",
                    "issue": "冗長な表現です。",
                    "suggestion": "拡張性が高いのが特徴です",
                },
                {
                    "category": "factual",
                    "severity": "medium",
                    "confidence": 0.6,
                    "location": {"fileName": "quiz_05.json", "unitId": "q-5", "field": "choices[1]"},
                    "excerpt": "拡張性が低い",
                    "issue": "事実誤認の可能性があります。",
                    "suggestion": "拡張性が低い可能性があります",
                },
            ]
        },
        file_name="quiz_05.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=quiz_content,
    )

    assert len(response.issues) == 2
    explanation_issue, choice_issue = response.issues
    assert explanation_issue.original == quiz_content["questions"][0]["explanation"]
    assert choice_issue.original == quiz_content["questions"][0]["choices"][1]
    assert explanation_issue.excerpt == "拡張性が高いのが特徴です。"


def test_text_quality_response_leaves_original_none_without_source_content() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_02.json", "unitId": "doc-2", "field": "text"},
                    "excerpt": "重要になります",
                    "issue": "断定的な表現です。",
                    "suggestion": "重要になる可能性があります",
                },
            ]
        },
        file_name="doc_02.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=None,
    )

    assert len(response.issues) == 1
    assert response.issues[0].original is None


# --- 置換型 suggestion（text mode）テスト ---


def test_text_replacement_suggestion_replaces_only_target_fragment_and_preserves_surrounding_text() -> None:
    """正常系: replaceFrom が1箇所一致 → original の該当箇所のみ置換、前後完全保持。"""
    content = {
        "type": "document",
        "documents": [
            {
                "id": "doc-34",
                "text": "受動態はpassivelyと訳される。受動態の概念は重要である。",
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_34.json", "unitId": "doc-34", "field": "text"},
                    "excerpt": "passively",
                    "issue": "受動的に訳す方が自然です。",
                    "replaceFrom": "passively",
                    "replaceTo": "受動的に",
                }
            ]
        },
        file_name="doc_34.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert len(response.issues) == 1
    issue = response.issues[0]
    assert issue.suggestion == "受動態は受動的にと訳される。受動態の概念は重要である。"
    assert issue.original == content["documents"][0]["text"]
    # 前後が保持されている
    assert "受動態は" in issue.suggestion
    assert "と訳される。" in issue.suggestion
    assert "受動態の概念は重要である。" in issue.suggestion


def test_text_replacement_suggestion_is_blank_when_replace_from_not_found() -> None:
    """fail-safe: 0件一致 → suggestion 空（適用不可）。"""
    content = {
        "type": "document",
        "documents": [
            {"id": "doc-1", "text": "元の本文をそのまま保持します。"}
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.7,
                    "location": {"fileName": "doc_x.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "存在しない断片",
                    "issue": "修正したいです。",
                    "replaceFrom": "存在しない断片です",
                    "replaceTo": "置換後",
                }
            ]
        },
        file_name="doc_x.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert len(response.issues) == 1
    assert response.issues[0].suggestion == ""


def test_text_replacement_suggestion_is_blank_when_replace_from_ambiguous() -> None:
    """fail-safe: 複数箇所一致 → suggestion 空（曖昧で適用不可）。"""
    content = {
        "type": "document",
        "documents": [
            {"id": "doc-1", "text": "重要な点を確認します。重要な点は忘れないでください。"}
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.7,
                    "location": {"fileName": "doc_x.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "重要な点",
                    "issue": "表現を直したいです。",
                    "replaceFrom": "重要な点",
                    "replaceTo": "大切な点",
                }
            ]
        },
        file_name="doc_x.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert len(response.issues) == 1
    assert response.issues[0].suggestion == ""


def test_text_replacement_suggestion_is_blank_when_result_is_drastically_shorter() -> None:
    """fail-safe: 結果が original の 50% 未満 → suggestion 空。"""
    long_text = "本文は十分な長さがあります。" * 20
    content = {
        "type": "document",
        "documents": [{"id": "doc-1", "text": long_text}],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "high",
                    "confidence": 0.9,
                    "location": {"fileName": "doc_x.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "本文は十分な長さがあります。本文は十分な長さがあります。",
                    "issue": "要約します。",
                    "replaceFrom": long_text,
                    "replaceTo": "短",
                }
            ]
        },
        file_name="doc_x.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert len(response.issues) == 1
    assert response.issues[0].suggestion == ""


def test_text_replacement_suggestion_falls_back_to_normalized_match_for_punctuation_and_whitespace() -> None:
    """正規化フォールバック: 空白・約物・全角半角差のみを救済する。"""
    content = {
        "type": "document",
        "documents": [
            {
                "id": "doc-1",
                "text": "クラウドは重要です。補足します。",
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_x.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "重要",
                    "issue": "強調します。",
                    # モデル側が空白や全角/半角の揺れを含むが、意味のある語句は一致する想定。
                    "replaceFrom": "クラウドは 重要 です",
                    "replaceTo": "クラウドは重要である",
                }
            ]
        },
        file_name="doc_x.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert len(response.issues) == 1
    # 正規化フォールバックで 1 箇所だけマッチし、置換は元テキスト上で行われる。
    # original の句点「。」は置換範囲外なので保持される。
    assert response.issues[0].suggestion == "クラウドは重要である。補足します。"


def test_text_replacement_suggestion_works_for_quiz_question_field() -> None:
    """quiz question フィールドで置換が効くこと。"""
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-5",
                "question": "クラウドの特徴として正しいものはどれか。",
                "choices": ["a", "b", "c", "d"],
                "answerIndex": 0,
                "explanation": "解説",
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz_05.json", "unitId": "q-5", "field": "question"},
                    "excerpt": "どれか",
                    "issue": "疑問符に直す方が適切です。",
                    "replaceFrom": "どれか",
                    "replaceTo": "どれですか",
                }
            ]
        },
        file_name="quiz_05.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert len(response.issues) == 1
    assert response.issues[0].suggestion == "クラウドの特徴として正しいものはどれですか。"
    assert response.issues[0].original == content["questions"][0]["question"]


def test_text_replacement_suggestion_works_for_quiz_choices_field() -> None:
    """quiz choices フィールドで置換が効くこと。"""
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-7",
                "question": "質問",
                "choices": ["拡張性が低い", "b", "c", "d"],
                "answerIndex": 0,
                "explanation": "解説",
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.7,
                    "location": {"fileName": "quiz_07.json", "unitId": "q-7", "field": "choices[1]"},
                    "excerpt": "b",
                    "issue": "断定を避けます。",
                    "replaceFrom": "b",
                    "replaceTo": "bの可能性がある",
                }
            ]
        },
        file_name="quiz_07.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert len(response.issues) == 1
    # choices[1] = "b" のみが置換される
    assert response.issues[0].suggestion == "bの可能性がある"
    assert response.issues[0].original == "b"


def test_text_replacement_suggestion_works_for_quiz_explanation_field() -> None:
    """quiz explanation フィールドで置換が効くこと。"""
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-9",
                "question": "質問",
                "choices": ["a", "b", "c", "d"],
                "answerIndex": 0,
                "explanation": "クラウドは必要な時に必要な分だけ利用できる。拡張性が高い。",
            }
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.7,
                    "location": {"fileName": "quiz_09.json", "unitId": "q-9", "field": "explanation"},
                    "excerpt": "拡張性が高い",
                    "issue": "冗長表現を整えます。",
                    "replaceFrom": "拡張性が高い",
                    "replaceTo": "高い拡張性",
                }
            ]
        },
        file_name="quiz_09.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert len(response.issues) == 1
    assert (
        response.issues[0].suggestion
        == "クラウドは必要な時に必要な分だけ利用できる。高い拡張性。"
    )
    assert response.issues[0].original == content["questions"][0]["explanation"]


def test_tts_replacement_suggestion_is_unaffected_by_text_mode_replacement_logic() -> None:
    """tts mode は置換型ロジックに影響を受けず、suggestion 全文を保持する。"""
    content = {
        "type": "document",
        "documents": [
            {"id": "doc-1", "text": "SQLとJSONを説明します。", "tts": {"text": "SQLとJSONを説明します。"}}
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "tts_text_mismatch",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "tts.text"},
                    "excerpt": "SQLとJSON",
                    "issue": "tts.text が元テキストと意味的にずれている可能性があります。",
                    "suggestion": "意味を一致させる",
                    "replaceFrom": "SQLとJSON",
                    "replaceTo": "意味を一致させる",
                }
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    # tts mode は置換型ロジックを通らないため、suggestion 全文がそのまま残る。
    assert len(response.issues) == 1
    assert response.issues[0].suggestion == "意味を一致させる"


def test_text_replacement_suggestion_skips_issue_when_replace_from_missing() -> None:
    """replaceFrom フィールドが欠落した issue は旧動作に従い、空 suggestion は維持されない。"""
    content = {
        "type": "document",
        "documents": [{"id": "doc-1", "text": "本文"}],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "style",
                    "severity": "medium",
                    "confidence": 0.7,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "本文",
                    "issue": "何か",
                    "suggestion": "",
                }
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TEXT_QUALITY_CATEGORIES,
        source_content=content,
    )
    # 旧仕様: テキストモードで suggestion 空 + replaceFrom なしは issue として残らない
    assert response.issues == []


def test_tts_quality_response_does_not_attach_original() -> None:
    content = {
        "type": "document",
        "documents": [
            {"id": "doc-1", "text": "ITを説明します。", "tts": {"text": "アイティーを説明します。"}},
        ],
    }
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "tts_text_mismatch",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc_01.json", "unitId": "doc-1", "field": "tts.text"},
                    "excerpt": "IT",
                    "issue": "意味のずれを確認してください。",
                    "suggestion": "アイティー",
                },
            ]
        },
        file_name="doc_01.json",
        model="test-model",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        source_content=content,
    )

    assert len(response.issues) == 1
    assert response.issues[0].original is None


def test_quality_check_invalid_llm_response_is_error(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs: object) -> dict:
        return {"issues": [{"category": "unknown"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/text-check", json={"target": target})

    assert response.status_code == 502
    assert "response validation failed" in response.json()["detail"]


def test_quality_check_llm_failure_does_not_fallback_to_mock(tmp_path, monkeypatch) -> None:
    target = _write_document_pack(tmp_path, monkeypatch)
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    response = client.post("/quality/text-check", json={"target": target})

    assert response.status_code == 502
    assert "LLM call failed" in response.json()["detail"]


def test_quality_check_retries_temporary_failures() -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def generate() -> dict:
        calls["count"] += 1
        if calls["count"] < 3:
            raise RuntimeError("retry me")
        return {"issues": []}

    result = _generate_json_with_retry(generate, attempts=3, initial_delay=0.1, sleep=sleeps.append)

    assert result == {"issues": []}
    assert calls["count"] == 3
    assert sleeps == [0.1, 0.2]


def test_quality_check_missing_pack_returns_404(tmp_path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "local_storage_dir", str(tmp_path / "generated"))
    monkeypatch.setattr(settings, "gcs_prefix", "sokqa")
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    response = client.post(
        "/quality/text-check",
        json={
            "target": {
                "creatorId": "creator_missing",
                "contentId": "content_missing",
                "versionId": "v20260612_120000",
                "packName": "doc_01.json",
                "kind": "document",
            }
        },
    )

    assert response.status_code == 404


def test_build_text_replacement_suggestion_logs_replace_from_replace_to_and_built_ok(caplog) -> None:
    """_build_text_replacement_suggestion が replaceFrom/replaceTo/built/original_chars/built_chars を記録するか検証。
    q-30 相当(伝聞調の置換)を疑似的に再現し、ログから経路を追跡できることを担保する。"""
    caplog.set_level("INFO", logger="app.services.quality_checker")

    issue = QualityIssue(
        category="style",
        severity="medium",
        confidence=0.8,
        location=QualityLocation(fileName="quiz_30.json", unitId="q-30", field="explanation"),
        excerpt="重要だとされている",
        issue="伝聞調を断定に直します。",
    )
    original = "この設定は重要だとされているため遵守してください。"

    _build_text_replacement_suggestion(issue, original, "重要だとされている", "重要です")

    # キーが出力されることを検証(値の完全一致固定は避ける)
    assert "quality_check.replacement_built" in caplog.text
    assert "replace_from=" in caplog.text
    assert "replace_to=" in caplog.text
    assert "built=ok" in caplog.text
    assert "original_chars=" in caplog.text
    assert "built_chars=" in caplog.text
    assert "file=quiz_30.json" in caplog.text
    assert "unit_id=q-30" in caplog.text


def test_build_text_replacement_suggestion_logs_built_fail_when_match_misses(caplog) -> None:
    """replaceFrom が original に存在しない場合 built=fail が記録され、suggestion は空になる。
    この経路でも replace_from/replace_to/original_chars が記録され、後段追跡が可能であることを検証。"""
    caplog.set_level("INFO", logger="app.services.quality_checker")

    issue = QualityIssue(
        category="style",
        severity="medium",
        confidence=0.7,
        location=QualityLocation(fileName="doc_18.json", unitId="doc-18", field="text"),
        excerpt="にな-ないます",
        issue="混入記号を除去します。",
    )
    original = "この役割はシステムの安定性をにないます。"
    # original に存在しない断片を replace_from に与えて失敗経路を通す
    result = _build_text_replacement_suggestion(issue, original, "にな-ないます", "になないます")

    assert result.suggestion == ""
    assert "quality_check.replacement_built" in caplog.text
    assert "replace_from=" in caplog.text
    assert "replace_to=" in caplog.text
    assert "built=fail" in caplog.text


def test_build_text_replacement_suggestion_logs_broken_replace_to_for_q30_breakage_path(caplog) -> None:
    """LLM が壊れた replaceTo(意味改変)を返した場合でも、経路が記録されることを検証。
    q-30 の「重要だ重要な」破損経路を疑似的に再現し、ログから原因を追跡できることを担保。"""
    caplog.set_level("INFO", logger="app.services.quality_checker")

    issue = QualityIssue(
        category="style",
        severity="medium",
        confidence=0.7,
        location=QualityLocation(fileName="quiz_30.json", unitId="q-30", field="explanation"),
        excerpt="重要だとされている",
        issue="伝聞調を直します。",
    )
    original = "この設定は重要だとされているため遵守してください。"
    # LLM が壊れた replaceTo を返した想定
    result = _build_text_replacement_suggestion(issue, original, "重要だとされている", "重要だ重要な")

    assert "quality_check.replacement_built" in caplog.text
    assert "replace_from=" in caplog.text
    assert "replace_to=" in caplog.text
    assert "built=ok" in caplog.text
    # 壊れた replaceTo が適用されて suggestion が原始と異なること(破損経路成立の確認)
    assert result.suggestion != original
    assert "重要だ重要な" in result.suggestion


def test_multilingual_filter_removes_closing_tag_suggestion() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz.json", "unitId": "q-1", "field": "text"},
                    "excerpt": "Good morning",
                    "issue": "英語スパンに閉じタグを付与します。",
                    "suggestion": "[en-US]Good morning[/en-US]",
                }
            ]
        },
        file_name="quiz.json",
        model="test",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        allow_language_tags=True,
    )

    assert response.issues == []


def test_multilingual_filter_keeps_closing_tag_suggestion_for_normal_pack() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz.json", "unitId": "q-1", "field": "text"},
                    "excerpt": "Good morning",
                    "issue": "英語スパンに閉じタグを付与します。",
                    "suggestion": "[en-US]Good morning[/en-US]",
                }
            ]
        },
        file_name="quiz.json",
        model="test",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        allow_language_tags=False,
    )

    assert len(response.issues) == 1


def test_multilingual_filter_removes_latin_span_katakana_rewrite() -> None:
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "quiz.json", "unitId": "q-1", "field": "text"},
                    "excerpt": "Please read the text",
                    "issue": "英文をカタカナ化します。",
                    "suggestion": "プリーズ・リード・ザ・テキスト",
                }
            ]
        },
        file_name="quiz.json",
        model="test",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        allow_language_tags=True,
    )

    assert response.issues == []


def test_multilingual_filter_keeps_mixed_japanese_katakana_reading() -> None:
    # 日本語文の一部(カタカナ語)の読み補正は誤爆しないこと(単一言語と同様に残る)。
    # excerpt に日本語文字を含むため、純ラテンスパンのカタカナ化とは判定されない。
    response = _quality_response_from_data(
        {
            "issues": [
                {
                    "category": "reading",
                    "severity": "medium",
                    "confidence": 0.8,
                    "location": {"fileName": "doc.json", "unitId": "doc-1", "field": "text"},
                    "excerpt": "データベースはDBです",
                    "issue": "略語の読みです。",
                    "suggestion": "データベースはディービーです",
                }
            ]
        },
        file_name="doc.json",
        model="test",
        max_issues=50,
        allowed_categories=TTS_QUALITY_CATEGORIES,
        allow_language_tags=True,
    )

    assert len(response.issues) == 1


def test_is_multilingual_invalid_tts_suggestion_detects_closing_tag_and_katakana() -> None:
    closing_tag_issue = QualityIssue(
        category="reading",
        severity="medium",
        confidence=0.8,
        location=QualityLocation(fileName="quiz.json", unitId="q-1", field="text"),
        excerpt="Good morning",
        issue="閉じタグ付与",
        suggestion="[en-US]Good morning[/en-US]",
    )
    katakana_issue = QualityIssue(
        category="reading",
        severity="medium",
        confidence=0.8,
        location=QualityLocation(fileName="quiz.json", unitId="q-1", field="text"),
        excerpt="TPO",
        issue="カタカナ化",
        suggestion="ティーピーオー",
    )
    valid_issue = QualityIssue(
        category="reading",
        severity="medium",
        confidence=0.8,
        location=QualityLocation(fileName="doc.json", unitId="doc-1", field="text"),
        excerpt="SQLとJSON",
        issue="略語の読み",
        suggestion="エスキューエルとジェイソン",
    )

    assert _is_multilingual_invalid_tts_suggestion(closing_tag_issue, None) is True
    assert _is_multilingual_invalid_tts_suggestion(katakana_issue, None) is True
    assert _is_multilingual_invalid_tts_suggestion(valid_issue, None) is False

