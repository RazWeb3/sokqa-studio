import logging

import pytest
from app.config import Settings, get_settings
from app.schemas.common import TtsLanguageSettings, TtsRule, default_speech_language_code
from app.schemas.request import GeneratePackRequest, PlanPackRequest
from app.schemas.sokqa import CoursePlan, GeneratedFile, QuizTts
from app.services.gemini_client import GeminiClient
from app.services.generation.context import GenerationContext
from app.services.tts_optimizer import (
    MAX_TTS_FILE_CHARS,
    _gemini_document_chunk_readings,
    _guard_llm_text,
    _speech_text,
    _mode_or_default,
    _normalize_language_tag_markup,
    _tts_batch_quiz_prompt,
    _tts_quiz_question_prompt,
    _tts_reading_prompt,
    _tts_reading_rules_block,
    optimize_generated_files_with_report,
    validate_tts_files,
)


def _doc_file() -> GeneratedFile:
    return GeneratedFile(
        name="doc_01.json",
        kind="document",
        content={
            "id": "pack_doc_01",
            "type": "document",
            "schemaVersion": 1,
            "title": "Git確認",
            "language": "ja",
            "documents": [
                {
                    "id": "doc-1",
                    "text": ".gitconfig と .gitlog を確認します. バージョン 1.2 も確認します。",
                },
                {
                    "id": "doc-2",
                    "text": ".gitignore と .env と git init を確認します。",
                },
            ],
        },
    )


def _quiz_file() -> GeneratedFile:
    return GeneratedFile(
        name="quiz_01.json",
        kind="quiz",
        content={
            "id": "pack_quiz_01",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "Git確認クイズ",
            "language": "ja",
            "questions": [
                {
                    "id": "q-1",
                    "question": "git init の説明として正しいものはどれですか？",
                    "choices": [
                        "リポジトリを初期化する",
                        ".gitignore を削除する",
                        "設定値を表示する",
                        "DBを作成する",
                    ],
                    "answerIndex": 0,
                    "explanation": "git init は現在のディレクトリをGitリポジトリとして初期化します。",
                },
                {
                    "id": "q-2",
                    "question": ".gitconfig を確認する理由は何ですか？",
                    "choices": [
                        "ユーザー設定を確認するため",
                        "JSONを削除するため",
                        "CPUを交換するため",
                        "UIを隠すため",
                    ],
                    "answerIndex": 0,
                    "explanation": ".gitconfig にはGitのユーザー設定などが保存されます。",
                },
            ],
        },
    )


def _multi_question_quiz_file(count: int = 12) -> GeneratedFile:
    questions = []
    for index in range(1, count + 1):
        questions.append(
            {
                "id": f"q-{index}",
                "question": f"git init と .gitignore の確認問題 {index} ですか？",
                "choices": [
                    "リポジトリを初期化する",
                    ".gitignore を削除する",
                    "設定値を表示する",
                    "DBを作成する",
                ],
                "answerIndex": 0,
                "explanation": f"git init は現在のディレクトリをGitリポジトリとして初期化します。問題 {index} の解説です。",
            }
        )
    return GeneratedFile(
        name="quiz_many.json",
        kind="quiz",
        content={
            "id": "pack_quiz_many",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "Git確認クイズ",
            "language": "ja",
            "questions": questions,
        },
    )


def _plain_quiz_file() -> GeneratedFile:
    return GeneratedFile(
        name="quiz_plain.json",
        kind="quiz",
        content={
            "id": "pack_quiz_plain",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "確認クイズ",
            "language": "ja",
            "questions": [
                {
                    "id": "q-plain",
                    "question": "次の説明として正しいものはどれですか?",
                    "choices": [
                        "保存します",
                        "確認します",
                        "終了します",
                        "開始します",
                    ],
                    "answerIndex": 0,
                    "explanation": "保存する操作を選びます.",
                }
            ],
        },
    )


def _indonesian_quiz_file() -> GeneratedFile:
    return GeneratedFile(
        name="quiz_id.json",
        kind="quiz",
        content={
            "id": "pack_quiz_id",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "Salam Quiz",
            "language": "id",
            "questions": [
                {
                    "id": "q-id",
                    "question": "Salam pagi yang tepat adalah apa?",
                    "choices": [
                        "おはよう",
                        "おはようございます",
                        "おじゃまします",
                        "はじめまして",
                    ],
                    "answerIndex": 1,
                    "explanation": "おはようございます は salam pagi yang sopan.",
                }
            ],
        },
    )


def _plain_doc_file() -> GeneratedFile:
    return GeneratedFile(
        name="doc_plain.json",
        kind="document",
        content={
            "id": "pack_doc_plain",
            "type": "document",
            "schemaVersion": 1,
            "title": "通常文書",
            "language": "ja",
            "documents": [
                {
                    "id": "doc-plain",
                    "text": "保存する操作を確認します。",
                }
            ],
        },
    )


def _ai_quiz_file() -> GeneratedFile:
    return GeneratedFile(
        name="ai_quiz_integrated_review.json",
        kind="quiz",
        content={
            "id": "ai_quiz_integrated_review",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "AI総合・応用クイズ",
            "language": "ja",
            "questions": [
                {
                    "id": "q-20",
                    "question": "AI 活用で最も適切な対応はどれですか?",
                    "choices": [
                        "AIの提案を業務要件と照合する",
                        "AIの出力を無条件に採用する",
                        "記録を残さずAIだけで判断する",
                        "担当者に確認する",
                    ],
                    "answerIndex": 0,
                    "explanation": "AI の出力は業務要件や責任分担と照らして確認します。",
                }
            ],
        },
    )


def _sized_document_file(lengths: list[int]) -> GeneratedFile:
    return GeneratedFile(
        name="doc_sized.json",
        kind="document",
        content={
            "id": "pack_doc_sized",
            "type": "document",
            "schemaVersion": 1,
            "title": "長文文書",
            "language": "ja",
            "documents": [
                {
                    "id": f"doc-{index}",
                    "text": "あ" * length,
                }
                for index, length in enumerate(lengths, start=1)
            ],
        },
    )


def _sized_quiz_file(lengths: list[int]) -> GeneratedFile:
    return GeneratedFile(
        name="quiz_sized.json",
        kind="quiz",
        content={
            "id": "pack_quiz_sized",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "長文クイズ",
            "language": "ja",
            "questions": [
                {
                    "id": f"q-{index}",
                    "question": "あ" * length,
                    "choices": ["選択肢A", "選択肢B", "選択肢C", "選択肢D"],
                    "answerIndex": 0,
                    "explanation": "解説です。",
                }
                for index, length in enumerate(lengths, start=1)
            ],
        },
    )


def test_default_tts_reading_mode_is_llm() -> None:
    assert Settings(_env_file=None).tts_reading_mode == "llm"


def test_explicit_tts_reading_mode_overrides_default() -> None:
    assert _mode_or_default("none") == "none"
    assert _mode_or_default("rule") == "rule"
    assert _mode_or_default("llm") == "llm"
    assert _mode_or_default("multilingual") == "multilingual"


def test_legacy_auto_tts_reading_mode_is_normalized_to_llm() -> None:
    plan_request = PlanPackRequest(
        theme="Git入門",
        targetUser="社会人",
        ttsReadingMode="auto",
    )
    plan = CoursePlan(
        id="git_intro",
        title="Git入門",
        description="Gitを学ぶ",
        language="ja",
        targetUser="社会人",
        difficulty="beginner",
        documents=[{"id": "doc_01", "title": "Git概要", "goal": "Gitを理解する"}],
        quizPacks=[{"id": "quiz_01", "title": "Git確認", "purpose": "key_concepts", "questionCount": 4}],
        ttsReadingMode="auto",
    )
    generate_request = GeneratePackRequest(plan=plan, ttsReadingMode="auto")

    assert plan_request.ttsReadingMode == "llm"
    assert plan.ttsReadingMode == "llm"
    assert generate_request.ttsReadingMode == "llm"


def test_disabled_legacy_tts_fields_normalize_to_none() -> None:
    plan = CoursePlan(
        id="no_tts",
        title="TTSなし",
        description="TTSを作らない",
        language="ja",
        targetUser="社会人",
        difficulty="beginner",
        documents=[{"id": "doc_01", "title": "概要", "goal": "理解する"}],
        quizPacks=[{"id": "quiz_01", "title": "確認", "purpose": "key_concepts", "questionCount": 4}],
        enableTtsOptimize=False,
        ttsReadingMode="llm",
    )

    assert plan.ttsReadingMode == "none"


def test_language_codes_are_normalized_and_speech_defaults_are_known() -> None:
    request = PlanPackRequest(theme="韓国語基礎", targetUser="社会人", language="pt-br")

    assert request.language == "pt-BR"
    assert default_speech_language_code("ja") == "ja-JP"
    assert default_speech_language_code("en") == "en-US"
    assert default_speech_language_code("zh") == "zh-CN"
    assert default_speech_language_code("ko") == "ko-KR"
    assert default_speech_language_code("pt") == "pt-PT"
    assert default_speech_language_code("id") == "id-ID"


def test_rule_mode_reports_ascii_left_after_partial_dot_replacement(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    files, report = optimize_generated_files_with_report([_doc_file()], [], mode="rule")

    first = files[0].content["documents"][0]["tts"]["text"]
    assert "ドット ギットconfig" in first
    assert "ドット ギットlog" in first
    assert first.endswith("確認します。")
    assert ". バージョン 1.2" in first
    assert any(issue.issueType == "ascii_after_dot_reading" for issue in report.issues)
    assert any(issue.suggestedRuleSource == ".gitconfig" for issue in report.issues)
    assert report.llmGeneratedIds == []


# ── _guard_llm_text space-diff guard ──────────────────────────────

def test_guard_llm_removes_surplus_spaces_when_only_space_differs() -> None:
    """LLM出力とfallbackの差が半角スペースのみでLLM側に余分がある場合、余分スペースを除去したvalueを返す。"""
    warnings: list = []
    result = _guard_llm_text(
        "役割も にないます",        # LLM output: surplus space between も and に
        "役割もにないます",          # fallback: rule-based, no space
        source_text="役割も担います",
        file_name="test.json",
        item_id="doc-18",
        field="text",
        warnings=warnings,
    )
    assert result == "役割もにないます"
    assert warnings == []


def test_guard_llm_preserves_value_when_content_differs_beyond_spaces() -> None:
    """LLM出力とfallbackがスペース除去後も不一致（=読み補助を含む）の場合、LLM出力をそのまま保持する。"""
    warnings: list = []
    result = _guard_llm_text(
        "いってんに も確認します",  # LLM output with intentional reading-assist space
        "1.2 も確認します",          # fallback: rule-based, different content
        source_text="1.2 も確認します",
        file_name="test.json",
        item_id="doc-1",
        field="text",
        warnings=warnings,
    )
    assert result == "いってんに も確認します"
    assert warnings == []


def test_guard_llm_preserves_value_when_identical_to_fallback() -> None:
    """LLM出力とfallbackが完全一致の場合、値を一切変更せずそのまま返す。"""
    warnings: list = []
    result = _guard_llm_text(
        "エーアイ の出力",
        "エーアイ の出力",
        source_text="AI の出力",
        file_name="test.json",
        item_id="doc-1",
        field="text",
        warnings=warnings,
    )
    assert result == "エーアイ の出力"
    assert warnings == []


def test_guard_llm_preserves_value_when_fallback_none_or_empty() -> None:
    """fallbackがNoneまたは空の場合、従来通りvalueをそのまま保持する。"""
    for fb in [None, ""]:
        warnings: list = []
        result = _guard_llm_text(
            "役割も にないます",
            fb,
            source_text="役割も担います",
            file_name="test.json",
            item_id="doc-18",
            field="text",
            warnings=warnings,
        )
        assert result == "役割も にないます"
        assert warnings == []


def test_guard_llm_falls_back_when_katakana_inside_foreign_span() -> None:
    """multilingual モードで [en-US] スパン内にカタカナが混入した場合、原文フォールバックを返す。"""
    warnings: list = []
    result = _guard_llm_text(
        "フライト状況を尋ねます。[en-US]Excuse me, flight ジェイエルひゃくにじゅうさん to ロンドン.[ja-JP] これは丁寧な表現です。",
        "フライト状況を尋ねます。[en-US]Excuse me, flight JL123 to London.[ja-JP] これは丁寧な表現です。",
        source_text="フライト状況を尋ねます。Excuse me, flight JL123 to London. これは丁寧な表現です。",
        file_name="doc_01.json",
        item_id="doc-1",
        field="text",
        warnings=warnings,
        allow_language_tags=True,
    )
    assert result == "フライト状況を尋ねます。[en-US]Excuse me, flight JL123 to London.[ja-JP] これは丁寧な表現です。"
    assert len(warnings) == 1
    assert warnings[0].issueType == "foreign_span_katakana"


def test_guard_llm_keeps_foreign_span_when_english_is_intact() -> None:
    """[en-US] スパン内が正しい英語のままなら、フォールバックせず保持する。"""
    warnings: list = []
    value = "フライト状況を尋ねます。[en-US]Excuse me, flight JL123 to London.[ja-JP] これは丁寧な表現です。"
    result = _guard_llm_text(
        value,
        value,
        source_text="フライト状況を尋ねます。Excuse me, flight JL123 to London. これは丁寧な表現です。",
        file_name="doc_01.json",
        item_id="doc-1",
        field="text",
        warnings=warnings,
        allow_language_tags=True,
    )
    assert result == value
    assert warnings == []


def test_guard_llm_keeps_katakana_in_default_span() -> None:
    """デフォルト言語(ja)スパン内のカタカナは対象外（フォールバックしない）。"""
    warnings: list = []
    value = "[ja-JP]サムソナイトのスーツケースについて尋ねます。[en-US]Where is my Samsonite?[ja-JP] と伝えてください。"
    result = _guard_llm_text(
        value,
        value,
        source_text="サムソナイトのスーツケースについて尋ねます。Where is my Samsonite? と伝えてください。",
        file_name="doc_01.json",
        item_id="doc-13",
        field="text",
        warnings=warnings,
        allow_language_tags=True,
    )
    assert result == value
    assert warnings == []


def test_rule_mode_applies_document_rules_without_extra_punctuation_conversion(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    files, _ = optimize_generated_files_with_report([_doc_file()], [], mode="rule")
    docs = files[0].content["documents"]

    assert docs[1]["tts"]["text"] == "ドット ギットイグノア と ドット イーエヌブイ と ギット イニット を確認します。"


def test_rule_mode_applies_placeholder_fallbacks_for_existing_material(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    files = [
        GeneratedFile(
            name="doc_placeholders.json",
            kind="document",
            content={
                "id": "pack_doc_placeholders",
                "type": "document",
                "schemaVersion": 1,
                "title": "プレースホルダー文書",
                "language": "ja",
                "documents": [
                    {
                        "id": "doc-legacy-ja",
                        "text": "株式会社◯◯に連絡し、答えは＿＿＿です。",
                    },
                    {
                        "id": "doc-legacy-en",
                        "text": "The answer is _____.",
                    },
                ],
            },
        )
    ]

    optimized, _ = optimize_generated_files_with_report(files, [], mode="rule")
    docs = optimized[0].content["documents"]

    assert docs[0]["tts"]["text"] == "株式会社まるまるに連絡し、答えは  です。"
    assert docs[1]["tts"]["text"] == "The answer is  ."
    assert "◯◯" not in docs[0]["tts"]["text"]
    assert "＿＿＿" not in docs[0]["tts"]["text"]
    assert "_____" not in docs[1]["tts"]["text"]


def test_rule_mode_omits_document_tts_when_reading_matches_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    files, _ = optimize_generated_files_with_report([_plain_doc_file()], [], mode="rule")
    doc = files[0].content["documents"][0]

    assert "tts" not in doc


def test_llm_mode_omits_document_tts_when_reading_matches_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "doc-plain",
                    "text": "保存する操作を確認します。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_plain_doc_file()], [], mode="llm")
    doc = files[0].content["documents"][0]

    assert "tts" not in doc


def test_llm_mode_omits_matching_document_tts_and_keeps_changed_document_tts(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_mixed.json",
        kind="document",
        content={
            "id": "pack_doc_mixed",
            "type": "document",
            "schemaVersion": 1,
            "title": "混在文書",
            "language": "ja",
            "documents": [
                {
                    "id": "doc-plain",
                    "text": "保存する操作を確認します。",
                },
                {
                    "id": "doc-ai",
                    "text": "AI の出力を確認します。",
                },
            ],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "doc-plain",
                    "text": "保存する操作を確認します。",
                },
                {
                    "id": "doc-ai",
                    "text": "エーアイ の出力を確認します。",
                },
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report(
        [file],
        [TtsRule(source="AI", reading="エーアイ")],
        mode="llm",
    )
    docs = files[0].content["documents"]

    assert "tts" not in docs[0]
    assert docs[1]["tts"]["text"] == "エーアイ の出力を確認します。"


def test_llm_document_processes_one_file_in_one_call_within_20000_chars(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = _sized_document_file([MAX_TTS_FILE_CHARS - 7000, 6000])
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        calls.append(prompt)
        return {
            "items": [
                {"id": document["id"], "text": "ヨミ"}
                for document in file.content["documents"]
                if f'- id: {document["id"]}' in prompt
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [], mode="llm")
    documents = files[0].content["documents"]

    assert len(calls) == 1
    assert [document["id"] for document in documents] == ["doc-1", "doc-2"]
    assert {document["id"] for document in documents if "tts" in document} == {"doc-1", "doc-2"}
    assert report.llmGeneratedIds == ["doc-1", "doc-2"]


def test_llm_document_splits_only_above_20000_chars_and_preserves_ids(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = _sized_document_file([9000, 9000, 3000])
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        calls.append(prompt)
        return {
            "items": [
                {"id": document["id"], "text": "ヨミ"}
                for document in file.content["documents"]
                if f'- id: {document["id"]}' in prompt
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [], mode="llm")
    documents = files[0].content["documents"]

    assert len(calls) == 2
    assert [document["id"] for document in documents] == ["doc-1", "doc-2", "doc-3"]
    assert {document["id"] for document in documents if "tts" in document} == {"doc-1", "doc-2", "doc-3"}
    assert report.llmGeneratedIds == ["doc-1", "doc-2", "doc-3"]


def test_llm_prompt_keeps_original_punctuation_instruction() -> None:
    prompt = _tts_reading_prompt("確認します。", [])
    rules_block = _tts_reading_rules_block([])

    for text in [prompt, rules_block]:
        assert 'Keep the original Japanese punctuation as-is. Do not convert sentence-ending "。" to "、", and do not add or remove punctuation.' in text
        assert 'A period "." between digits or inside numbers/codes must stay as the source; do not convert it.' in text
        assert 'normalize sentence endings "。" and "." to "、"' not in text
        assert "〜します。 -> 〜します、" not in text


def test_tts_prompts_define_reading_assistant_role_without_fixed_samples() -> None:
    quiz_prompt = _tts_quiz_question_prompt(
        "q-role",
        "重複とJSONを確認しますか？",
        ["JSONを確認する", "保存する", "削除する", "開始する"],
        "重複とJSONの読みを確認します。",
        [],
    )
    batch_prompt = _tts_batch_quiz_prompt(
        [
            type(
                "Question",
                (),
                {
                    "id": "q-role-batch",
                    "question": "重複とJSONを確認しますか？",
                    "choices": ["JSONを確認する", "保存する", "削除する", "開始する"],
                    "explanation": "重複とJSONの読みを確認します。",
                },
            )()
        ],
        [],
    )
    prompts = [_tts_reading_prompt("重複を確認します。", []), _tts_reading_rules_block([]), quiz_prompt, batch_prompt]
    for text in prompts:
        assert "あなたの役割は、表示用文章を編集することではなく、読み上げ用テキストを作成することです。" in text
        assert "守る対象: 文の意味、構造、語順、助詞、句読点、文体。" in text
        assert "変更してよい対象: 読み補助が必要な語句" in text
        assert "読み補助が不要な語句は変更しないでください。" in text
        assert "文章全体を読み仮名へ変換してはいけません。" in text
        assert "pronunciation-sensitive terms" in text
        assert "株式会社サトウ" not in text
        assert "John Smith" not in text


def test_quiz_tts_schema_keeps_answer_text_but_removes_choices_text() -> None:
    assert "answerText" in QuizTts.model_fields
    assert "choicesText" not in QuizTts.model_fields


def test_llm_mode_generates_kana_for_unknown_dot_words_and_keeps_core_rules(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        calls.append(prompt)
        return {
            "items": [
                {
                    "id": "doc-2",
                    "text": ".gitignore と .env と git init を確認します。",
                },
                {
                    "id": "doc-1",
                    "text": "ドット ギットコンフィグ と ドット ギットログ を確認します、バージョン いってんに も確認します、",
                },
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([_doc_file()], [], mode="llm")
    docs = files[0].content["documents"]

    assert docs[0]["tts"]["text"] == "ドット ギットコンフィグ と ドット ギットログ を確認します、バージョン いってんに も確認します、"
    assert docs[1]["tts"]["text"] == "ドット ギットイグノア と ドット イーエヌブイ と ギット イニット を確認します。"
    assert report.issues == []
    assert "doc-1" in report.llmGeneratedIds
    assert "doc-2" in report.llmGeneratedIds
    assert len(calls) == 1


def test_llm_document_tts_falls_back_when_unexpected_script_appears(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_mojibake.json",
        kind="document",
        content={
            "id": "pack_doc_mojibake",
            "type": "document",
            "schemaVersion": 1,
            "title": "CRM確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "CRMを確認します。"}],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {"items": [{"id": "doc-1", "text": "シーアールエム의確認をします。"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [TtsRule(source="CRM", reading="シーアールエム")], mode="llm")
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "シーアールエムを確認します。"
    assert any(issue.issueType == "unexpected_script" and issue.field == "text" for issue in report.issues)


def test_llm_document_tts_falls_back_when_source_kanji_mostly_disappears(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_kanji_loss.json",
        kind="document",
        content={
            "id": "pack_doc_kanji_loss",
            "type": "document",
            "schemaVersion": 1,
            "title": "JSON確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "会社ではJSONを利用します。"}],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {"items": [{"id": "doc-1", "text": "かいしゃではジェイソンをりようします。"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [TtsRule(source="JSON", reading="ジェイソン")], mode="llm")
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "会社ではジェイソンを利用します。"
    assert any(issue.issueType == "source_kanji_loss" and issue.field == "text" for issue in report.issues)


def test_llm_document_tts_keeps_partial_reading_corrections_when_source_kanji_remain(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_partial_reading.json",
        kind="document",
        content={
            "id": "pack_doc_partial_reading",
            "type": "document",
            "schemaVersion": 1,
            "title": "読み補助確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "重複とJSONを確認します。"}],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {"items": [{"id": "doc-1", "text": "ちょうふくとジェイソンを確認します。"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [TtsRule(source="JSON", reading="ジェイソン")], mode="llm")
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "ちょうふくとジェイソンを確認します。"
    assert not any(issue.issueType == "source_kanji_loss" for issue in report.issues)
    assert not any(issue.issueType == "particle_sequence_edit" for issue in report.issues)


def test_llm_document_tts_falls_back_when_particle_sequence_edit_appears(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_particle_edit.json",
        kind="document",
        content={
            "id": "pack_doc_particle_edit",
            "type": "document",
            "schemaVersion": 1,
            "title": "助詞崩壊確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "互いの身分とJSONを確認します。"}],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {"items": [{"id": "doc-1", "text": "たがいのをみぶんとジェイソンを確認します。"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [TtsRule(source="JSON", reading="ジェイソン")], mode="llm")
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "互いの身分とジェイソンを確認します。"
    assert any(issue.issueType == "particle_sequence_edit" and issue.field == "text" for issue in report.issues)


def test_llm_document_tts_falls_back_for_non_cjk_foreign_scripts(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    samples = [
        "シーアールエム業界ের確認をします。",
        "シーアールエムж確認をします。",
        "シーアールエムم確認をします。",
    ]

    for sample in samples:
        file = GeneratedFile(
            name="doc_foreign_script.json",
            kind="document",
            content={
                "id": "pack_doc_foreign_script",
                "type": "document",
                "schemaVersion": 1,
                "title": "CRM確認",
                "language": "ja",
                "documents": [{"id": "doc-1", "text": "CRMを確認します。"}],
            },
        )

        def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
            return {"items": [{"id": "doc-1", "text": sample}]}

        monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

        files, report = optimize_generated_files_with_report([file], [TtsRule(source="CRM", reading="シーアールエム")], mode="llm")
        tts = files[0].content["documents"][0]["tts"]

        assert tts["text"] == "シーアールエムを確認します。"
        assert any(issue.issueType == "unexpected_script" and issue.field == "text" for issue in report.issues)


def test_llm_document_tts_keeps_allowed_japanese_latin_and_symbols(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_allowed_script.json",
        kind="document",
        content={
            "id": "pack_doc_allowed_script",
            "type": "document",
            "schemaVersion": 1,
            "title": "CRM確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "CRMとＡＩ、GitHub v1.2を確認します。"}],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {"items": [{"id": "doc-1", "text": "シーアールエムとエーアイ、GitHub v1.2を確認します。"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [TtsRule(source="CRM", reading="シーアールエム")], mode="llm")
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "シーアールエムとエーアイ、GitHub v1.2を確認します。"
    assert not any(issue.issueType == "unexpected_script" for issue in report.issues)


def test_llm_quiz_tts_falls_back_only_for_field_with_unexpected_script(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="quiz_mojibake.json",
        kind="quiz",
        content={
            "id": "pack_quiz_mojibake",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "CRM確認クイズ",
            "language": "ja",
            "questions": [
                {
                    "id": "q-1",
                    "question": "CRMの説明として正しいものはどれですか？",
                    "choices": ["CRMを使う", "紙で管理する", "保存しない", "削除する"],
                    "answerIndex": 0,
                    "explanation": "CRMは顧客関係管理です。",
                }
            ],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-1",
                    "questionText": "シーアールエムの説明として正しいものはどれですか？",
                    "choices": [{"index": 0, "text": "シーアールエム의使う"}],
                    "explanationText": "シーアールエムは顧客関係管理です。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [TtsRule(source="CRM", reading="シーアールエム")], mode="llm")
    tts = files[0].content["questions"][0]["tts"]

    assert tts["questionText"] == "シーアールエムの説明として正しいものはどれですか？"
    assert tts["choiceTexts"] == ["シーアールエムを使う", "", "", ""]
    assert tts["explanationText"] == "シーアールエムは顧客関係管理です。"
    assert any(issue.issueType == "unexpected_script" and issue.field == "choiceTexts.0" for issue in report.issues)


def test_none_mode_skips_tts_generation(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    files, report = optimize_generated_files_with_report([_doc_file()], [], mode="none")
    docs = files[0].content["documents"]

    assert "tts" not in docs[0]
    assert "tts" not in docs[1]
    assert report.mode == "none"
    assert report.issues == []
    assert report.llmGeneratedIds == []


def test_tts_report_detects_raw_period_and_duplicate_punctuation() -> None:
    file = GeneratedFile(
        name="doc_01.json",
        kind="document",
        content={
            "id": "pack_doc_01",
            "type": "document",
            "schemaVersion": 1,
            "title": "Git確認",
            "language": "ja",
            "documents": [
                {
                    "id": "doc-1",
                    "text": ".gitconfig を確認します。",
                    "tts": {"text": "ドット ギットconfig を確認します、、README.md も確認します、"},
                }
            ],
        },
    )

    report = validate_tts_files([file], mode="rule")
    issue_types = {issue.issueType for issue in report.issues}
    assert {"ascii_after_dot_reading", "raw_period", "duplicate_punctuation"}.issubset(issue_types)


def test_tts_report_does_not_flag_hiragana_dominant_text_or_natural_particles() -> None:
    file = GeneratedFile(
        name="doc_no_false_positive.json",
        kind="document",
        content={
            "id": "pack_doc_no_false_positive",
            "type": "document",
            "schemaVersion": 1,
            "title": "誤検出確認",
            "language": "ja",
            "documents": [
                {"id": "doc-1", "text": "ひらがなが多いです。", "tts": {"text": "ひらがなが多いです。"}},
                {"id": "doc-2", "text": "今日はあさです。", "tts": {"text": "きょうはあさです。"}},
                {"id": "doc-3", "text": "わたしは東京にいます。", "tts": {"text": "わたしはとうきょうにいます。"}},
                {"id": "doc-4", "text": "資料を確認してから、案内を送ります。", "tts": {"text": "資料を確認してから、案内を送ります。"}},
                {"id": "doc-5", "text": "仕様だと理解しやすい説明です。", "tts": {"text": "仕様だと理解しやすい説明です。"}},
                {"id": "doc-6", "text": "目的とは異なる結果です。", "tts": {"text": "目的とは異なる結果です。"}},
                {"id": "doc-7", "text": "画面に応じて表示します。", "tts": {"text": "画面に応じて表示します。"}},
            ],
        },
    )

    report = validate_tts_files([file], mode="rule")

    assert not any(issue.issueType == "source_kanji_loss" for issue in report.issues)
    assert not any(issue.issueType == "particle_sequence_edit" for issue in report.issues)


def test_plan_rules_still_override_llm_output(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {"id": "doc-1", "text": ".git を確認します。"},
                {"id": "doc-2", "text": ".git を確認します。"},
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report(
        [_doc_file()],
        [TtsRule(source=".git", reading="ドット ジット")],
        mode="llm",
    )

    assert "ドット ジット" in files[0].content["documents"][0]["tts"]["text"]


def test_llm_quiz_batches_questions_and_reuses_answer_choice(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        calls.append(prompt)
        return {
            "items": [
                {
                    "id": "q-1",
                    "questionText": "ギット イニット の説明として正しいものはどれですか？",
                    "choices": [
                        {"index": 0, "text": "リポジトリをしょきかする"},
                        {"index": 1, "text": "ドット ギットイグノア を削除する"},
                        {"index": 2, "text": "せっていちを表示する"},
                        {"index": 3, "text": "データベースを作成する"},
                    ],
                    "explanationText": "ギット イニット は現在のディレクトリをギットリポジトリとして初期化します。",
                },
                {
                    "id": "q-2",
                    "questionText": "ドット ギットコンフィグ を確認する理由は何ですか？",
                    "choices": [
                        {"index": 0, "text": "ユーザー設定を確認するため"},
                        {"index": 1, "text": "ジェイソンを削除するため"},
                        {"index": 2, "text": "CPUを交換するため"},
                        {"index": 3, "text": "ユーアイを隠すため"},
                    ],
                    "explanationText": "ドット ギットコンフィグ にはギットのユーザー設定などが保存されます。",
                },
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([_quiz_file()], [], mode="llm")
    questions = files[0].content["questions"]

    assert len(calls) == 1
    assert "Questions:" in calls[0]
    assert "- id: q-1" in calls[0]
    assert "- id: q-2" in calls[0]
    assert questions[0]["tts"].get("answerText") is None
    assert "choicesText" not in questions[0]["tts"]
    assert questions[0]["tts"]["choiceTexts"][0] == "リポジトリをしょきかする"
    assert questions[0]["tts"]["choiceTexts"][1] == "ドット ギットイグノア を削除する"
    assert "answerText" not in questions[0]["tts"]
    assert report.llmGeneratedIds == ["q-1", "q-2"]


def test_llm_quiz_batch_accepts_top_level_item_array(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> list:
        return [
            {
                "id": "q-1",
                "questionText": "ギット イニット の説明として正しいものはどれですか？",
                "choices": [
                    {"index": 0, "text": "リポジトリを初期化する"},
                    {"index": 1, "text": "ドット ギットイグノア を削除する"},
                    {"index": 2, "text": "設定値を表示する"},
                    {"index": 3, "text": "データベースを作成する"},
                ],
                "explanationText": "ギット イニット の解説です。",
            },
            {
                "id": "q-2",
                "questionText": "ドット ギットコンフィグ を確認する理由は何ですか？",
                "choices": [
                    {"index": 0, "text": "ユーザー設定を確認するため"},
                    {"index": 1, "text": "ジェイソンを削除するため"},
                    {"index": 2, "text": "CPUを交換するため"},
                    {"index": 3, "text": "ユーアイを隠すため"},
                ],
                "explanationText": "ドット ギットコンフィグ にはギットのユーザー設定などが保存されます。",
            },
        ]

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([_quiz_file()], [], mode="llm")
    questions = files[0].content["questions"]

    assert questions[1]["tts"]["choiceTexts"][1] == "ジェイソンを削除するため"
    assert report.llmGeneratedIds == ["q-1", "q-2"]


def test_llm_quiz_uses_chunk_count_instead_of_question_count(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        calls.append(prompt)
        items = []
        for index in range(1, 13):
            question_id = f"q-{index}"
            if f"- id: {question_id}" not in prompt:
                continue
            items.append(
                {
                    "id": question_id,
                    "questionText": f"ギット イニット の確認問題 {index} ですか？",
                    "choices": [
                        {"index": 0, "text": f"リポジトリをしょきかする {index}"},
                        {"index": 1, "text": "ドット ギットイグノア を削除する"},
                        {"index": 2, "text": "せっていちを表示する"},
                        {"index": 3, "text": "データベースを作成する"},
                    ],
                    "explanationText": f"ギット イニット の解説 {index} です。",
                }
            )
        return {"items": items}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([_multi_question_quiz_file()], [], mode="llm")
    questions = files[0].content["questions"]

    assert len(questions) == 12
    assert len(calls) == 1
    assert len(calls) < len(questions)
    assert all(question["tts"]["questionText"] for question in questions)
    assert all("choicesText" not in question["tts"] for question in questions)
    assert all(question["tts"]["choiceTexts"] for question in questions)
    assert all(question["tts"].get("answerText") is None for question in questions)
    assert all(question["tts"]["explanationText"] for question in questions)
    assert all("番" not in "".join(question["tts"]["choiceTexts"]) for question in questions)
    assert all("answerText" not in question["tts"] for question in questions)
    assert report.llmGeneratedIds == sorted(f"q-{index}" for index in range(1, 13))


def test_llm_quiz_uses_same_20000_char_file_split_rule_and_preserves_ids(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    within_limit_file = _sized_quiz_file([MAX_TTS_FILE_CHARS - 7000, 6000])
    over_limit_file = _sized_quiz_file([9000, 9000, 3000])
    within_calls: list[str] = []
    over_calls: list[str] = []

    def fake_generate_json_within(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        within_calls.append(prompt)
        return {
            "items": [
                {
                    "id": question["id"],
                    "questionText": "ヨミ",
                    "choices": [{"index": index, "text": f"ヨミ{index}"} for index, _ in enumerate(question["choices"])],
                    "explanationText": "ヨミ",
                }
                for question in within_limit_file.content["questions"]
                if f'- id: {question["id"]}' in prompt
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json_within)
    within_files, within_report = optimize_generated_files_with_report([within_limit_file], [], mode="llm")

    def fake_generate_json_over(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        over_calls.append(prompt)
        return {
            "items": [
                {
                    "id": question["id"],
                    "questionText": "ヨミ",
                    "choices": [{"index": index, "text": f"ヨミ{index}"} for index, _ in enumerate(question["choices"])],
                    "explanationText": "ヨミ",
                }
                for question in over_limit_file.content["questions"]
                if f'- id: {question["id"]}' in prompt
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json_over)
    over_files, over_report = optimize_generated_files_with_report([over_limit_file], [], mode="llm")
    within_questions = within_files[0].content["questions"]
    over_questions = over_files[0].content["questions"]

    assert len(within_calls) == 1
    assert [question["id"] for question in within_questions] == ["q-1", "q-2"]
    assert {question["id"] for question in within_questions if "tts" in question} == {"q-1", "q-2"}
    assert within_report.llmGeneratedIds == ["q-1", "q-2"]
    assert len(over_calls) == 2
    assert [question["id"] for question in over_questions] == ["q-1", "q-2", "q-3"]
    assert {question["id"] for question in over_questions if "tts" in question} == {"q-1", "q-2", "q-3"}
    assert over_report.llmGeneratedIds == ["q-1", "q-2", "q-3"]


def test_llm_single_question_path_falls_back_on_top_level_array(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    big_question = GeneratedFile(
        name="quiz_big.json",
        kind="quiz",
        content={
            "id": "pack_quiz_big",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "長文クイズ",
            "language": "ja",
            "questions": [
                {
                    "id": "q-big",
                    "question": " ".join(["CRM"] * 5000),
                    "choices": ["CRM", "保存", "削除", "確認"],
                    "answerIndex": 0,
                    "explanation": "CRM を確認します。",
                }
            ],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> list:
        return [{"text": "invalid"}]

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report(
        [big_question],
        [TtsRule(source="CRM", reading="シーアールエム")],
        mode="llm",
    )
    tts = files[0].content["questions"][0]["tts"]

    assert tts["questionText"].startswith("シーアールエム")
    assert report.llmGeneratedIds == []


def test_llm_quiz_omits_choice_texts_when_choices_match_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-plain",
                    "questionText": "次の説明として正しいものはどれですか?",
                    "choices": [
                        {"index": 0, "text": "保存します"},
                        {"index": 1, "text": "確認します"},
                        {"index": 2, "text": "終了します"},
                        {"index": 3, "text": "開始します"},
                    ],
                    "explanationText": "保存する操作を選びます.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_plain_quiz_file()], [], mode="llm")
    question = files[0].content["questions"][0]

    assert "tts" not in question


def test_llm_quiz_outputs_no_language_tags_by_default(monkeypatch) -> None:
    """llm モードは言語タグを出力しない。プロンプトで禁止指示され、LLM が誤ってタグを出しても残存タグは機械除去される。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert "Do not output language tags" in prompt
        return {
            "items": [
                {
                    "id": "q-plain",
                    "questionText": "次の説明として正しいものはどれですか?",
                    "choices": [
                        {"index": 0, "text": "[ja-JP]保存します"},
                        {"index": 1, "text": "[ja-JP]確認します"},
                        {"index": 2, "text": "続けます?"},
                        {"index": 3, "text": "本当です？"},
                    ],
                    "explanationText": "保存する操作を選びます.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_plain_quiz_file()], [], mode="llm")
    question = files[0].content["questions"][0]
    tts = question["tts"]

    assert "choicesText" not in tts
    assert "questionText" not in tts
    assert tts.get("answerText") is None
    assert len(tts["choiceTexts"]) == len(question["choices"])
    for value in [str(tts.get("explanationText", "")), *tts.get("choiceTexts", [])]:
        assert "[en-US]" not in value
        assert "[ja-JP]" not in value


def test_multilingual_quiz_outputs_choice_texts_and_preserves_language_tags(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert 'scenario default language is "ja"' in prompt
        return {
            "items": [
                {
                    "id": "q-plain",
                    "questionText": "次の説明として正しいものはどれですか?",
                    "choices": [
                        {"index": 0, "text": "[en-US]Save it、"},
                        {"index": 1, "text": "OK."},
                        {"index": 2, "text": "続けます?"},
                        {"index": 3, "text": "本当です？"},
                    ],
                    "explanationText": "[en-US]Save it? [ja-JP]を選びます.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_plain_quiz_file()], [], mode="multilingual")
    tts = files[0].content["questions"][0]["tts"]

    assert tts["choiceTexts"] == ["[en-US]Save it", "OK.", "続けます?", "本当です？"]
    assert tts["explanationText"] == "[en-US]Save it? [ja-JP]を選びます."


def test_learning_language_tags_and_keeps_source_equal_choice_texts(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="english_choices.json",
        kind="quiz",
        content={
            "id": "english_choices",
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

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-1",
                    "questionText": "朝の挨拶はどれですか。",
                    "choices": [
                        {"index": 0, "text": "Good morning"},
                        {"index": 1, "text": "Hello"},
                        {"index": 2, "text": "Good evening"},
                        {"index": 3, "text": "Goodbye"},
                    ],
                    "explanationText": "朝は [en-US]Good morning[ja-JP] を使います。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    files, _ = optimize_generated_files_with_report(
        [file], [], mode="multilingual",
        context=GenerationContext("language_learning", "ja", "en", "learning", "multilingual"),
    )

    # choiceTexts is retained because no choicesLanguage is present: switch
    # tags alone do not make the common fallback locale explicit.
    tts = files[0].content["questions"][0]["tts"]
    assert "choiceTexts" in tts


def test_learning_mode_omits_choice_texts_when_choices_language_present(monkeypatch) -> None:
    """問題③: learningモードで choicesLanguage が指定されている場合、選択肢は学習言語で
    そのまま読めるため choiceTexts は冗長。省略して choices へフォールバックさせ、
    choicesLanguage は保持される。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="english_choices_learning.json",
        kind="quiz",
        content={
            "id": "english_choices_learning",
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

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-1",
                    "questionText": "朝の挨拶はどれですか。",
                    "choices": [
                        {"index": 0, "text": "[en-US]Good morning"},
                        {"index": 1, "text": "[en-US]Hello"},
                        {"index": 2, "text": "[en-US]Good evening"},
                        {"index": 3, "text": "[en-US]Goodbye"},
                    ],
                    "explanationText": "朝は [en-US]Good morning[ja-JP] を使います。",
                    "choicesLanguage": "en-US",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    files, _ = optimize_generated_files_with_report(
        [file], [], mode="multilingual",
        context=GenerationContext("language_learning", "ja", "en", "auto", "multilingual"),
    )

    tts = files[0].content["questions"][0]["tts"]
    # 冗長な choiceTexts は省略される
    assert "choiceTexts" not in tts
    # choicesLanguage は欠落なく保持される（エンジンが choices を en-US で読む）
    assert tts.get("choicesLanguage") == "en-US"


def test_learning_language_auto_keeps_only_non_pack_choice_texts(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="auto_languages.json",
        kind="quiz",
        content={
            "id": "auto_languages",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "英会話",
            "language": "ja",
            "learningLanguage": "en",
            "choiceLanguageMode": "auto",
            "questions": [
                {
                    "id": "q-en",
                    "question": "英語を選んでください。",
                    "choices": ["Good morning", "Hello", "Good evening", "Goodbye"],
                    "answerIndex": 0,
                    "explanation": "英語4択です。",
                },
                {
                    "id": "q-ja",
                    "question": "意味を選んでください。",
                    "choices": ["おはよう", "こんにちは", "こんばんは", "さようなら"],
                    "answerIndex": 0,
                    "explanation": "日本語4択です。",
                },
            ],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": question["id"],
                    "questionText": question["question"],
                    "choices": [
                        {"index": index, "text": choice}
                        for index, choice in enumerate(question["choices"])
                    ],
                    "explanationText": question["explanation"],
                }
                for question in file.content["questions"]
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    files, _ = optimize_generated_files_with_report(
        [file], [], mode="multilingual",
        context=GenerationContext("language_learning", "ja", "en", "auto", "multilingual"),
    )
    questions = files[0].content["questions"]

    assert all(value.startswith("[en-US]") for value in questions[0]["tts"]["choiceTexts"])
    assert "tts" not in questions[1] or "choiceTexts" not in questions[1]["tts"]


def test_learning_mode_cjk_keeps_choice_texts_for_reading_correction(monkeypatch) -> None:
    """問題③修正: choiceLanguageMode=learning でも学習言語が CJK/ハングル（読み補正要）の場合は
    choiceTexts を削除せず維持する。ラテン系学習言語（en 等）のみ冗長として省略される。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    # 日本語学習（pack=en, learning=ja / japanese スクリプト）→ choiceTexts 維持
    ja_file = GeneratedFile(
        name="ja_learning.json",
        kind="quiz",
        content={
            "id": "ja_learning",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "語学",
            "language": "en",
            "learningLanguage": "ja",
            "choiceLanguageMode": "learning",
            "questions": [
                {
                    "id": "q-1",
                    "question": "意味はどれですか。",
                    "choices": ["教室", "学校", "先生", "学生"],
                    "answerIndex": 0,
                    "explanation": "説明。",
                }
            ],
        },
    )

    def fake_ja(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-1",
                    "questionText": "意味はどれですか。",
                    "choices": [
                        {"index": 0, "text": "教室"},
                        {"index": 1, "text": "学校"},
                        {"index": 2, "text": "先生"},
                        {"index": 3, "text": "学生"},
                    ],
                    "explanationText": "説明。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_ja)
    ja_files, _ = optimize_generated_files_with_report(
        [ja_file], [], mode="multilingual",
        context=GenerationContext("language_learning", "en", "ja", "learning", "multilingual"),
    )
    tts_ja = ja_files[0].content["questions"][0].get("tts", {})
    assert "choiceTexts" in tts_ja

    # 英語学習（pack=ja, learning=en / latin スクリプト）→ 冗長 choiceTexts は省略
    en_file = GeneratedFile(
        name="en_learning.json",
        kind="quiz",
        content={
            "id": "en_learning",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "語学",
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

    def fake_en(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-1",
                    "questionText": "朝の挨拶はどれですか。",
                    "choices": [
                        {"index": 0, "text": "[en-US]Good morning"},
                        {"index": 1, "text": "[en-US]Hello"},
                        {"index": 2, "text": "[en-US]Good evening"},
                        {"index": 3, "text": "[en-US]Goodbye"},
                    ],
                    "explanationText": "朝は [en-US]Good morning[ja-JP] を使います。",
                    "choicesLanguage": "en-US",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_en)
    en_files, _ = optimize_generated_files_with_report([en_file], [], mode="multilingual")
    tts_en = en_files[0].content["questions"][0].get("tts", {})
    assert "choiceTexts" not in tts_en


def test_multilingual_prompt_includes_field_language_policy(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    seen_prompts: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        seen_prompts.append(prompt)
        return {
            "items": [
                {
                    "id": "q-plain",
                    "questionText": "다음 설명으로 올바른 것은 무엇입니까?",
                    "choices": [{"index": 0, "text": "저장합니다"}],
                    "explanationText": "저장하는 작업을 고릅니다.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    language_settings = TtsLanguageSettings(
        questionLanguageMode="select",
        questionLanguage="ko",
        choicesLanguageMode="mixed",
        choicesLanguage="ko",
        explanationLanguageMode="select",
        explanationLanguage="ko",
    )

    optimize_generated_files_with_report([_plain_quiz_file()], [], mode="multilingual", language_settings=language_settings)

    prompt = seen_prompts[0]
    assert "Field language policy:" in prompt
    assert "questionText: read this field in ko (ko-KR)" in prompt
    assert "choiceTexts: mixed-language field" in prompt
    assert "tag every span boundary explicitly" in prompt
    assert "同一言語が連続する区間は、まとめて1つのタグ区間として囲んでください。" in prompt
    assert "疑問符・感嘆符などの記号で終わる短い表現も、1つ残らず全てタグ対象です。" in prompt
    assert "explanationText: read this field in ko (ko-KR)" in prompt


def test_multilingual_prompt_includes_english_boundary_examples() -> None:
    language_settings = TtsLanguageSettings(
        questionLanguageMode="mixed",
        questionLanguage="en",
        choicesLanguageMode="mixed",
        choicesLanguage="en",
        explanationLanguageMode="mixed",
        explanationLanguage="en",
    )

    prompt = _tts_quiz_question_prompt(
        "q-en",
        "次の英語表現として自然なものはどれですか。",
        ["I couldn't agree more.", "Are you okay?"],
        "相手を気遣うときの短い表現も確認します。",
        [],
        language="ja",
        allow_language_tags=True,
        language_settings=language_settings,
    )

    assert 'Good: "[en-US]I couldn\'t agree more.[ja-JP] という表現は、強い同意を丁寧に伝えます。"' in prompt
    assert 'Bad: "I couldn\'t agree more. という表現は、強い同意を丁寧に伝えます。"' in prompt
    assert 'Good: "相手を気遣うときは [en-US]Are you okay?[ja-JP] と尋ねます。"' in prompt
    assert 'Bad: "相手を気遣うときは Are you okay? と尋ねます。"' in prompt


def test_multilingual_select_non_default_choice_language_prefixes_each_choice_without_closing_default(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert 'scenario default language is "id"' in prompt
        assert "choiceTexts: read this field in ja (ja-JP)" in prompt
        return {
            "items": [
                {
                    "id": "q-id",
                    "questionText": "Salam pagi yang tepat adalah apa?",
                    "choices": [
                        {"index": 0, "text": "おはよう[id-ID]"},
                        {"index": 1, "text": "[ja-JP]おはようございます[id-ID]"},
                        {"index": 2, "text": "おじゃまします"},
                        {"index": 3, "text": "はじめまして"},
                    ],
                    "explanationText": "おはようございます は salam pagi yang sopan.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    language_settings = TtsLanguageSettings(choicesLanguageMode="select", choicesLanguage="ja")

    files, _ = optimize_generated_files_with_report([_indonesian_quiz_file()], [], mode="multilingual", language_settings=language_settings)
    question = files[0].content["questions"][0]
    choice_texts = question["tts"]["choiceTexts"]

    assert choice_texts == ["[ja-JP]おはよう", "[ja-JP]おはようございます", "[ja-JP]おじゃまします", "[ja-JP]はじめまして"]
    assert all(not value.endswith("[id-ID]") for value in choice_texts)
    assert len(choice_texts) == len(question["choices"])
    assert all(choice_texts)
    assert question["answerIndex"] == 1
    assert question["choices"] == ["おはよう", "おはようございます", "おじゃまします", "はじめまして"]


def test_multilingual_select_default_choice_language_omits_tags(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    file = _indonesian_quiz_file()
    file.content["questions"][0]["choices"] = [
        "Teman akrab atau keluarga",
        "Guru di sekolah",
        "Atasan di kantor",
        "Orang yang baru pertama kali ditemui",
    ]

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-id",
                    "questionText": "Salam pagi yang tepat adalah apa?",
                    "choices": [
                        {"index": 0, "text": "[id-ID]Teman akrab atau keluarga"},
                        {"index": 1, "text": "[id-ID]Guru di sekolah"},
                        {"index": 2, "text": "[id-ID]Atasan di kantor"},
                        {"index": 3, "text": "[id-ID]Orang yang baru pertama kali ditemui"},
                    ],
                    "explanationText": "Penjelasan.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    language_settings = TtsLanguageSettings(choicesLanguageMode="select", choicesLanguage="id")

    files, _ = optimize_generated_files_with_report([file], [], mode="multilingual", language_settings=language_settings)

    assert "choiceTexts" not in files[0].content["questions"][0].get("tts", {})


def test_multilingual_select_pack_choice_language_resolves_to_pack_language(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    file = _indonesian_quiz_file()
    file.content["questions"][0]["choices"] = [
        "Teman akrab atau keluarga",
        "Guru di sekolah",
        "Atasan di kantor",
        "Orang yang baru pertama kali ditemui",
    ]

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert "choiceTexts: read this field in id (id-ID)" in prompt
        return {
            "items": [
                {
                    "id": "q-id",
                    "questionText": "Salam pagi yang tepat adalah apa?",
                    "choices": [
                        {"index": 0, "text": "[id-ID]Teman akrab atau keluarga"},
                        {"index": 1, "text": "[id-ID]Guru di sekolah"},
                        {"index": 2, "text": "[id-ID]Atasan di kantor"},
                        {"index": 3, "text": "[id-ID]Orang yang baru pertama kali ditemui"},
                    ],
                    "explanationText": "Penjelasan.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    language_settings = TtsLanguageSettings(choicesLanguageMode="select", choicesLanguage="pack")

    files, _ = optimize_generated_files_with_report([file], [], mode="multilingual", language_settings=language_settings)

    assert "choiceTexts" not in files[0].content["questions"][0].get("tts", {})


def test_multilingual_select_non_default_choice_language_keeps_tags_when_text_matches_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = _indonesian_quiz_file()
    file.content["questions"][0]["choices"] = [
        "じこしょうかい",
        "おげんき",
        "ありがとうございます",
        "あいさつ",
    ]

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert "choiceTexts: read this field in ja (ja-JP)" in prompt
        return {
            "items": [
                {
                    "id": "q-id",
                    "questionText": "Salam pagi yang tepat adalah apa?",
                    "choices": [
                        {"index": 0, "text": "じこしょうかい"},
                        {"index": 1, "text": "おげんき"},
                        {"index": 2, "text": "ありがとうございます"},
                        {"index": 3, "text": "あいさつ"},
                    ],
                    "explanationText": "Penjelasan.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    language_settings = TtsLanguageSettings(choicesLanguageMode="select", choicesLanguage="ja")

    files, _ = optimize_generated_files_with_report([file], [], mode="multilingual", language_settings=language_settings)
    tts = files[0].content["questions"][0]["tts"]

    assert tts["choiceTexts"] == [
        "[ja-JP]じこしょうかい",
        "[ja-JP]おげんき",
        "[ja-JP]ありがとうございます",
        "[ja-JP]あいさつ",
    ]


def test_multilingual_mixed_choices_keep_only_needed_default_return_tags(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    file = _indonesian_quiz_file()
    file.content["questions"][0]["choices"] = [
        "Sedikit membungkukkan badan atau おじぎ sebagai tanda hormat",
        "Guru di sekolah",
        "Atasan di kantor",
        "Orang yang baru pertama kali ditemui",
    ]

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert "choiceTexts: mixed-language field" in prompt
        return {
            "items": [
                {
                    "id": "q-id",
                    "questionText": "Salam pagi yang tepat adalah apa?",
                    "choices": [
                        {"index": 0, "text": "[id-ID]Sedikit membungkukkan badan atau [ja-JP]おじぎ[id-ID] sebagai tanda hormat[id-ID]"},
                        {"index": 1, "text": ""},
                        {"index": 2, "text": "Atasan di kantor"},
                        {"index": 3, "text": "Orang yang baru pertama kali ditemui"},
                    ],
                    "explanationText": "Penjelasan.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    language_settings = TtsLanguageSettings(choicesLanguageMode="mixed", choicesLanguage="ja")

    files, _ = optimize_generated_files_with_report([file], [], mode="multilingual", language_settings=language_settings)
    choice_texts = files[0].content["questions"][0]["tts"]["choiceTexts"]

    assert choice_texts[0] == "Sedikit membungkukkan badan atau [ja-JP]おじぎ[id-ID] sebagai tanda hormat"
    assert len(choice_texts) == 4
    assert all(choice_texts)


def test_multilingual_document_allows_selected_korean_script(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_multilingual_ko.json",
        kind="document",
        content={
            "id": "pack_doc_multilingual_ko",
            "type": "document",
            "schemaVersion": 1,
            "title": "韓国語確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "韓国語の挨拶を確認します。"}],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert "document text: read this field in ko (ko-KR)" in prompt
        return {"items": [{"id": "doc-1", "text": "[ko-KR]안녕하세요를 확인합니다."}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    language_settings = TtsLanguageSettings(documentTextLanguageMode="select", documentTextLanguage="ko")

    files, report = optimize_generated_files_with_report([file], [], mode="multilingual", language_settings=language_settings)
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "[ko-KR]안녕하세요를 확인합니다."
    assert not any(issue.issueType == "unexpected_script" for issue in report.issues)


def test_multilingual_document_mixed_normalizes_language_tags(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_multilingual_id_ja.json",
        kind="document",
        content={
            "id": "pack_doc_multilingual_id_ja",
            "type": "document",
            "schemaVersion": 1,
            "title": "Salam Jepang",
            "language": "id",
            "documents": [{"id": "doc-1", "text": "Bahasa Jepang memiliki salam おはよう pada pagi hari."}],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert 'scenario default language is "id"' in prompt
        assert "document text: mixed-language field" in prompt
        assert "Do not output XML tags" in prompt
        return {
            "items": [
                {
                    "id": "doc-1",
                    "text": '<lang xml:lang="id-ID">Bahasa Jepang memiliki salam <lang xml:lang="ja-JP">おはよう</lang><lang xml:lang="id-ID"> pada pagi hari.</lang>',
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)
    language_settings = TtsLanguageSettings(documentTextLanguageMode="mixed", documentTextLanguage="ja")

    files, report = optimize_generated_files_with_report([file], [], mode="multilingual", language_settings=language_settings)
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "Bahasa Jepang memiliki salam [ja-JP]おはよう[id-ID] pada pagi hari."
    assert "<lang" not in tts["text"]
    assert "</lang>" not in tts["text"]
    assert not any(issue.issueType == "unexpected_script" for issue in report.issues)


def test_normalize_language_tag_markup_removes_bracket_closing_tags() -> None:
    assert _normalize_language_tag_markup("[en-US]Can you[/en-US]") == "[en-US]Can you"


def test_normalize_language_tag_markup_trims_spaces_adjacent_to_tags() -> None:
    assert _normalize_language_tag_markup("[en-US] Hello [ja-JP]") == "[en-US]Hello[ja-JP]"


def test_normalize_language_tag_markup_keeps_valid_switch_tags_unchanged() -> None:
    assert _normalize_language_tag_markup("[en-US]Hello[ja-JP]世界") == "[en-US]Hello[ja-JP]世界"


def test_multilingual_document_does_not_apply_katakana_dictionary_rules(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_multilingual_it.json",
        kind="document",
        content={
            "id": "pack_doc_multilingual_it",
            "type": "document",
            "schemaVersion": 1,
            "title": "IT確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "ITを確認します。"}],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert "IT -> アイティー" not in prompt
        return {"items": [{"id": "doc-1", "text": "[en-US]IT[ja-JP]を確認します。"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report(
        [file],
        [TtsRule(source="IT", reading="アイティー")],
        mode="multilingual",
    )
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "[en-US]IT[ja-JP]を確認します。"
    assert "アイティー" not in tts["text"]
    assert not any(issue.issueType == "unexpected_script" for issue in report.issues)


def test_llm_document_still_falls_back_for_korean_script_without_multilingual(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_llm_ko_noise.json",
        kind="document",
        content={
            "id": "pack_doc_llm_ko_noise",
            "type": "document",
            "schemaVersion": 1,
            "title": "韓国語ノイズ確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "CRMを確認します。"}],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {"items": [{"id": "doc-1", "text": "シーアールエム안녕하세요"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [TtsRule(source="CRM", reading="シーアールエム")], mode="llm")
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "シーアールエムを確認します。"
    assert any(issue.issueType == "unexpected_script" and issue.field == "text" for issue in report.issues)


def test_llm_quiz_falls_back_to_rules_when_batch_response_omits_question(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        calls.append(prompt)
        return {
            "items": [
                {
                    "id": "q-1",
                    "questionText": "ギット イニット の説明として正しいものはどれですか？",
                    "choices": [
                        {"index": 0, "text": "リポジトリを初期化する"},
                        {"index": 1, "text": "ドット ギットイグノア を削除する"},
                        {"index": 2, "text": "せっていちを表示する"},
                        {"index": 3, "text": "データベースを作成する"},
                    ],
                    "explanationText": "ギット イニット の解説です。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([_quiz_file()], [], mode="llm")
    questions = files[0].content["questions"]

    assert len(calls) == 1
    assert questions[0]["tts"]["questionText"].startswith("ギット イニット")
    assert questions[1]["tts"]["questionText"] == "ドット ギットconfig を確認する理由は何ですか？"
    assert "ドット ギットconfig" in questions[1]["tts"]["questionText"]
    assert questions[1]["tts"].get("answerText") is None
    assert "choicesText" not in questions[1]["tts"]
    assert "answerText" not in questions[1]["tts"]
    assert report.llmGeneratedIds == ["q-1", "q-2"]


def test_llm_document_tts_collapses_duplicate_katakana_parenthetical(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="doc_duplicate.json",
        kind="document",
        content={
            "id": "pack_doc_duplicate",
            "type": "document",
            "schemaVersion": 1,
            "title": "ESG確認",
            "language": "ja",
            "documents": [
                {
                    "id": "doc-1",
                    "text": "Governance（ガバナンス）は重要です。Social（社会）も確認します。",
                }
            ],
        },
    )

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "doc-1",
                    "text": "ガバナンス（ガバナンス）は重要です。ソーシャル（社会）も確認します。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([file], [], mode="llm")
    tts_text = files[0].content["documents"][0]["tts"]["text"]

    assert "ガバナンス（ガバナンス）" not in tts_text
    assert "ガバナンスは重要です。" in tts_text
    assert "ソーシャル（社会）" in tts_text


def test_tts_prompt_mentions_duplicate_katakana_parenthetical_rule() -> None:
    assert "Governance（ガバナンス） should become ガバナンス" in _tts_reading_prompt("Governance（ガバナンス）", [])
    assert "Governance（ガバナンス） should become ガバナンス" in _tts_reading_rules_block([])


def test_rule_mode_applies_quiz_rules_and_keeps_choice_delimiters(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    files, _ = optimize_generated_files_with_report([_quiz_file()], [], mode="rule")
    tts = files[0].content["questions"][0]["tts"]

    assert tts["questionText"] == "ギット イニット の説明として正しいものはどれですか？"
    assert "choicesText" not in tts
    assert tts["choiceTexts"][1] == "ドット ギットイグノア を削除する"
    assert tts.get("answerText") is None
    assert "answerText" not in tts


def test_rule_mode_omits_quiz_tts_when_all_fields_match_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    files, _ = optimize_generated_files_with_report([_plain_quiz_file()], [], mode="rule")
    question = files[0].content["questions"][0]

    assert "tts" not in question


def test_rule_mode_outputs_only_changed_quiz_tts_fields(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    rules = [TtsRule(source="AI", reading="エーアイ")]
    file = GeneratedFile(
        name="quiz_partial.json",
        kind="quiz",
        content={
            "id": "pack_quiz_partial",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "部分補正クイズ",
            "language": "ja",
            "questions": [
                {
                    "id": "q-partial",
                    "question": "次の説明として正しいものはどれですか?",
                    "choices": ["保存します", "確認します", "終了します", "開始します"],
                    "answerIndex": 0,
                    "explanation": "AI の出力を確認します。",
                }
            ],
        },
    )

    files, _ = optimize_generated_files_with_report([file], rules, mode="rule")
    tts = files[0].content["questions"][0]["tts"]

    assert "questionText" not in tts
    assert "choiceTexts" not in tts
    assert tts["explanationText"] == "エーアイ の出力を確認します。"
    assert "answerText" not in tts


def test_rule_mode_outputs_choice_texts_when_choice_rule_changes_reading(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    rules = [TtsRule(source="AI", reading="エーアイ")]

    files, _ = optimize_generated_files_with_report([_ai_quiz_file()], rules, mode="rule")
    tts = files[0].content["questions"][0]["tts"]

    assert tts["choiceTexts"] == [
        "エーアイの提案を業務要件と照合する",
        "エーアイの出力を無条件に採用する",
        "記録を残さずエーアイだけで判断する",
        "",
    ]
    assert "choicesText" not in tts
    assert tts.get("answerText") is None


def test_rule_mode_keeps_sparse_choice_text_index_mapping(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    rules = [TtsRule(source="記録", reading="きろく")]
    file = GeneratedFile(
        name="sparse_quiz.json",
        kind="quiz",
        content={
            "id": "sparse_quiz",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "疎配列確認",
            "language": "ja",
            "questions": [
                {
                    "id": "q-sparse",
                    "question": "次の説明として正しいものはどれですか?",
                    "choices": ["保存します", "確認します", "記録を残します", "開始します"],
                    "answerIndex": 2,
                    "explanation": "記録を残すことが重要です。",
                }
            ],
        },
    )

    files, _ = optimize_generated_files_with_report([file], rules, mode="rule")
    tts = files[0].content["questions"][0]["tts"]

    assert tts["choiceTexts"] == ["", "", "きろくを残します", ""]


def test_llm_batch_outputs_choice_texts_when_choice_reading_differs_from_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    rules = [TtsRule(source="AI", reading="エーアイ")]

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert "- id: q-20" in prompt
        return {
            "items": [
                {
                    "id": "q-20",
                    "questionText": "エーアイ 活用で最も適切な対応はどれですか?",
                    "choices": [
                        {"index": 0, "text": "エーアイの提案を業務要件と照合する"},
                        {"index": 1, "text": "エーアイの出力を無条件に採用する"},
                        {"index": 2, "text": "記録を残さずエーアイだけで判断する"},
                        {"index": 3, "text": "担当者に確認する"},
                    ],
                    "explanationText": "エーアイ の出力は業務要件や責任分担と照らして確認します。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_ai_quiz_file()], rules, mode="llm")
    tts = files[0].content["questions"][0]["tts"]

    assert tts["choiceTexts"] == [
        "エーアイの提案を業務要件と照合する",
        "エーアイの出力を無条件に採用する",
        "記録を残さずエーアイだけで判断する",
        "",
    ]
    assert "choicesText" not in tts
    assert tts.get("answerText") is None


def test_rule_mode_omits_choice_texts_when_only_choice_separators_differ(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    file = GeneratedFile(
        name="quiz_rule_punctuation.json",
        kind="quiz",
        content={
            "id": "pack_quiz_rule_punctuation",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "API確認クイズ",
            "language": "ja",
            "questions": [
                {
                    "id": "q-rule",
                    "question": "API の説明として正しいものはどれですか?",
                    "choices": ["保存します、", "OK.", "続けます?", "本当です？"],
                    "answerIndex": 0,
                    "explanation": "API の意味を確認します?",
                }
            ],
        },
    )

    files, _ = optimize_generated_files_with_report([file], [], mode="rule")
    question = files[0].content["questions"][0]

    assert "tts" not in question


def test_none_mode_skips_quiz_tts_generation(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    files, report = optimize_generated_files_with_report([_quiz_file()], [], mode="none")
    questions = files[0].content["questions"]

    assert "tts" not in questions[0]
    assert "tts" not in questions[1]
    assert report.mode == "none"
    assert report.issues == []
    assert report.llmGeneratedIds == []


def test_speech_text_tag_boundary_switches_rule_processing() -> None:
    """第2層: タグの有無で rule 置換・かな化の処理を切り替えることを仕様として固定する。

    タグ内（非デフォルト言語スパン）は rule 適用を及ぼさず原文を保持し、
    タグ外（デフォルト言語=日本語）は従来通り rule 置換する。
    この切り替えは _rules_for_mode(multilingual)=[] の偶然の非適用とは独立した防御層である。
    """
    rules = [
        TtsRule(source="API", reading="エーピーアイ"),
        TtsRule(source="CPU", reading="シーピーユー"),
        TtsRule(source="UN", reading="ユーエヌ"),
    ]

    cases = [
        # タグ内（非デフォルト言語スパン）は保持
        ("[en-US]API[ja-JP]", "[en-US]API[ja-JP]"),
        ("[en-US]CPU[ja-JP]", "[en-US]CPU[ja-JP]"),
        ("[en-US]UN[ja-JP]", "[en-US]UN[ja-JP]"),
        ("[en-US]According to UN projections[ja-JP]", "[en-US]According to UN projections[ja-JP]"),
        # タグ外（デフォルト言語=日本語）は従来通り変換
        ("APIを使います", "エーピーアイを使います"),
        ("CPUの性能", "シーピーユーの性能"),
        # 混在: タグ内は保持・タグ外は変換
        ("この[en-US]API[ja-JP]はCPUで動く", "この[en-US]API[ja-JP]はシーピーユーで動く"),
    ]

    for source, expected in cases:
        assert _speech_text(source, rules) == expected


def _english_choices_quiz_file() -> GeneratedFile:
    """pack 言語が ja で、選択肢が英語のクイズ（choices はパック言語と異なる）。"""
    return GeneratedFile(
        name="quiz_en_choices.json",
        kind="quiz",
        content={
            "id": "pack_quiz_en_choices",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "English choices quiz",
            "language": "ja",
            "questions": [
                {
                    "id": "q-en-choices",
                    "question": "Which greeting is most polite?",
                    "choices": [
                        "It's a pleasure to finally meet you.",
                        "Happy to meet you.",
                        "Pleased to meet you.",
                        "How do you do?",
                    ],
                    "answerIndex": 0,
                    "explanation": "The first option is the most polite greeting.",
                }
            ],
        },
    )


def test_choices_language_not_set_when_choices_match_pack_language(monkeypatch) -> None:
    """要件1: 選択肢がパック言語(ja)の場合、choicesLanguage は付与されない。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-plain",
                    "questionText": "つぎの説明として正しいものはどれですか?",
                    "choices": [
                        {"index": 0, "text": "保存します"},
                        {"index": 1, "text": "確認します"},
                        {"index": 2, "text": "終了します"},
                        {"index": 3, "text": "開始します"},
                    ],
                    "explanationText": "保存する操作を選びます.",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_plain_quiz_file()], [], mode="multilingual")
    tts = files[0].content["questions"][0]["tts"]

    assert "choicesLanguage" not in tts


def test_choices_language_set_when_choices_differ_from_pack_language(monkeypatch) -> None:
    """要件2: 選択肢がパック言語(ja)と異なる場合、choicesLanguage が付与される。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-en-choices",
                    "questionText": "Which greeting is most polite?",
                    "choices": [
                        {"index": 0, "text": "It's a pleasure to finally meet you."},
                        {"index": 1, "text": "Happy to meet you."},
                        {"index": 2, "text": "Pleased to meet you."},
                        {"index": 3, "text": "How do you do?"},
                    ],
                    "explanationText": "The first option is the most polite greeting.",
                    "choicesLanguage": "en-US",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_english_choices_quiz_file()], [], mode="multilingual")
    tts = files[0].content["questions"][0]["tts"]

    assert tts.get("choicesLanguage") == "en-US"


def test_omitted_choice_texts_fallback_to_choices(monkeypatch) -> None:
    """要件3: choiceTexts を省略（原文と同一）した場合、choices にフォールバックし、
    choicesLanguage で指定された言語のタグとして読み上げ用テキストが生成される。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-en-choices",
                    "questionText": "Which greeting is most polite?",
                    "choices": [
                        {"index": 0, "text": "It's a pleasure to finally meet you."},
                        {"index": 1, "text": "Happy to meet you."},
                        {"index": 2, "text": "Pleased to meet you."},
                        {"index": 3, "text": "How do you do?"},
                    ],
                    "explanationText": "The first option is the most polite greeting.",
                    "choicesLanguage": "en-US",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_english_choices_quiz_file()], [], mode="multilingual")
    tts = files[0].content["questions"][0]["tts"]

    assert "choiceTexts" not in tts
    # choicesLanguage は保持される
    assert tts.get("choicesLanguage") == "en-US"


def test_null_empty_whitespace_choice_texts_fallback_to_choices(monkeypatch) -> None:
    """要件4: null / "" / 空白のみは既存どおり choices[i] にフォールバックする（回帰防止）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-en-choices",
                    "questionText": "Which greeting is most polite?",
                    "choices": [
                        {"index": 0, "text": None},
                        {"index": 1, "text": ""},
                        {"index": 2, "text": "   "},
                        {"index": 3, "text": "How do you do?"},
                    ],
                    "explanationText": "The first option is the most polite greeting.",
                    "choicesLanguage": "en-US",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_english_choices_quiz_file()], [], mode="multilingual")
    tts = files[0].content["questions"][0]["tts"]

    assert "choiceTexts" not in tts
    assert tts.get("choicesLanguage") == "en-US"


def test_choices_language_applied_to_fallback_choices(monkeypatch) -> None:
    """要件5: choicesLanguage が指定されている場合、フォールバック先の choices がその言語として読み上げられる。

    choiceTexts を省略して choices にフォールバックしたとき、QuizTts.choicesLanguage が
    そのまま保持され、再生時に choices を choicesLanguage の言語で読み上げる。ここでは
    choicesLanguage が欠落なく保持されることと、実テキスト優先の choiceTexts[3] が
    [en-US] タグとして言語指定されていることを確認する。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-en-choices",
                    "questionText": "Which greeting is most polite?",
                    "choices": [
                        {"index": 0, "text": "It's a pleasure to finally meet you."},
                        {"index": 1, "text": "Happy to meet you."},
                        {"index": 2, "text": "Pleased to meet you."},
                        {"index": 3, "text": "[en-US]How do you do?"},
                    ],
                    "explanationText": "The first option is the most polite greeting.",
                    "choicesLanguage": "en-US",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_english_choices_quiz_file()], [], mode="multilingual")
    tts = files[0].content["questions"][0]["tts"]

    # フォールバック先 choices を en-US で読み上げるための choicesLanguage が保持される
    assert tts.get("choicesLanguage") == "en-US"
    assert "choiceTexts" not in tts


def test_choice_texts_real_text_takes_priority_over_fallback(monkeypatch) -> None:
    """要件6: choiceTexts に実テキストがある場合、従来どおり choiceTexts が優先される。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": "q-plain",
                    "questionText": "つぎの説明として正しいものはどれですか?",
                    "choices": [
                        {"index": 0, "text": "りぽじとりをしょきかする"},
                        {"index": 1, "text": ".gitignore を削除する"},
                        {"index": 2, "text": "設定値を表示する"},
                        {"index": 3, "text": "DBを作成する"},
                    ],
                    "explanationText": "git init は現在のディレクトリをGitリポジトリとして初期化します。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, _ = optimize_generated_files_with_report([_plain_quiz_file()], [], mode="multilingual")
    tts = files[0].content["questions"][0]["tts"]

    # 要素0 のみ読み補正あり → 実テキスト優先（choiceTexts[0] が保持される）。
    # 要素1-3 は空のため既存フォールバックで choices[i] に補填される（既存挙動維持）。
    choice_texts = tts.get("choiceTexts")
    assert choice_texts is not None
    assert choice_texts[0] == "りぽじとりをしょきかする"
    assert choice_texts[1] == ".gitignore を削除する"
    assert choice_texts[2] == "設定値を表示する"
    assert choice_texts[3] == "DBを作成する"


def test_choices_language_single_language_path_no_regression(monkeypatch) -> None:
    """要件7: 単一言語パス（rule モード）で choicesLanguage が混入せず既存挙動を維持する。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    rules = [TtsRule(source="AI", reading="エーアイ")]

    files, _ = optimize_generated_files_with_report([_ai_quiz_file()], rules, mode="rule")
    tts = files[0].content["questions"][0]["tts"]

    # rule モードは LLM 出力なし → choicesLanguage は付与されない（既存維持）
    assert "choicesLanguage" not in tts
    # 既存フォールバック（実テキスト優先）は維持される
    assert tts["choiceTexts"] == [
        "エーアイの提案を業務要件と照合する",
        "エーアイの出力を無条件に採用する",
        "記録を残さずエーアイだけで判断する",
        "",
    ]


def test_multilingual_document_assigns_tts_to_all_documents_without_fallback_log(monkeypatch, caplog) -> None:
    """ケース1: 通常 multilingual パックは、読み補正が必要な全 document に TTS を付与し、
    fallback ログを出さない。読み補正不要な(document-3)は差分なしで TTS 省略が既存仕様通り。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {
            "items": [
                {
                    "id": document["id"],
                    "text": "[en-US]AI[ja-JP]を確認します。" if document["id"] == "doc-1" else "保存します。",
                }
                for document in file.content["documents"]
            ]
        }

    file = GeneratedFile(
        name="doc_multi.json",
        kind="document",
        content={
            "id": "pack_doc_multi",
            "type": "document",
            "schemaVersion": 1,
            "title": "AI確認",
            "language": "ja",
            "documents": [
                {"id": "doc-1", "text": "AI を確認します。"},
                {"id": "doc-2", "text": "保存します。"},
                {"id": "doc-3", "text": "確認します。"},
            ],
        },
    )
    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    with caplog.at_level(logging.WARNING, logger="sokqa_course_pack_agent"):
        files, report = optimize_generated_files_with_report([file], [], mode="multilingual")

    documents = files[0].content["documents"]
    # 読み補正が必要な doc-1 は必ず TTS 付与（フォールバック欠落の回帰防止）
    assert documents[0]["tts"]["text"] == "[en-US]AI[ja-JP]を確認します。"
    assert [document["id"] for document in documents] == ["doc-1", "doc-2", "doc-3"]
    assert report.llmGeneratedIds == ["doc-1", "doc-2", "doc-3"]
    assert not any("llm_document_fallback" in record.message for record in caplog.records)


def test_multilingual_document_retries_on_transient_failure_then_succeeds(monkeypatch) -> None:
    """ケース2: Gemini 一時失敗はリトライで回復し、通常生成される。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    calls = {"count": 0}

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient upstream error")
        return {"items": [{"id": "doc-1", "text": "エーアイ を確認します。"}]}

    file = GeneratedFile(
        name="doc_retry.json",
        kind="document",
        content={
            "id": "pack_doc_retry",
            "type": "document",
            "schemaVersion": 1,
            "title": "AI確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "AI を確認します。"}],
        },
    )
    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [TtsRule(source="AI", reading="エーアイ")], mode="multilingual")

    assert calls["count"] == 2
    tts = files[0].content["documents"][0]["tts"]
    assert tts["text"] == "エーアイ を確認します。"
    assert "doc-1" in report.llmGeneratedIds


def test_multilingual_document_persistent_failure_raises_runtime_error(monkeypatch) -> None:
    """ケース3: Gemini 永続失敗は RuntimeError となり、不完全な教材は生成しない。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        raise RuntimeError("persistent upstream error")

    file = GeneratedFile(
        name="doc_fail.json",
        kind="document",
        content={
            "id": "pack_doc_fail",
            "type": "document",
            "schemaVersion": 1,
            "title": "AI確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "AI を確認します。"}],
        },
    )
    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    with pytest.raises(RuntimeError):
        optimize_generated_files_with_report([file], [], mode="multilingual")


def test_multilingual_document_missing_ids_in_response_raises_runtime_error(monkeypatch) -> None:
    """ケース3補足: レスポンスに id が欠落しても RuntimeError とし、不完全な教材を返さない。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        return {"items": []}

    file = GeneratedFile(
        name="doc_missing.json",
        kind="document",
        content={
            "id": "pack_doc_missing",
            "type": "document",
            "schemaVersion": 1,
            "title": "AI確認",
            "language": "ja",
            "documents": [{"id": "doc-1", "text": "AI を確認します。"}],
        },
    )
    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    with pytest.raises(RuntimeError):
        optimize_generated_files_with_report([file], [], mode="multilingual")


def test_document_chunk_readings_retries_per_chunk_and_propagates_failure(monkeypatch) -> None:
    """ケース2/3補足: チャンク単位で独立リトライし、永続失敗チャンクのみ RuntimeError を伝播する。

    サイズオーバーで分割された複数チャンクを想定し、_gemini_document_chunk_readings 単体で
    チャンクごとのリトライ独立性与否を検証する。
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "gemini")
    attempts: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        if "- id: doc-ok" in prompt:
            attempts.append("ok")
            return {"items": [{"id": "doc-ok", "text": "成功。"}]}
        attempts.append("fail")
        raise RuntimeError("persistent chunk failure")

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    ok_chunk = [("doc-ok", "保存します。")]
    fail_chunk = [("doc-fail", "確認します。")]

    # 成功チャンクは1回で完了
    assert _gemini_document_chunk_readings(ok_chunk, 0, [], "ja", False, None) == {"doc-ok": "成功。"}
    assert attempts.count("ok") == 1

    # 失敗チャンクは最大3回リトライして RuntimeError
    with pytest.raises(RuntimeError):
        _gemini_document_chunk_readings(fail_chunk, 1, [], "ja", False, None)
    assert attempts.count("fail") == 3
