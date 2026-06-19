from app.config import Settings, get_settings
from app.schemas.common import TtsRule
from app.schemas.sokqa import GeneratedFile, QuizTts
from app.services.gemini_client import GeminiClient
from app.services.tts_optimizer import _mode_or_default, _tts_reading_prompt, _tts_reading_rules_block, optimize_generated_files_with_report, validate_tts_files


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


def test_default_tts_reading_mode_is_llm() -> None:
    assert Settings(_env_file=None).tts_reading_mode == "llm"


def test_explicit_tts_reading_mode_overrides_default() -> None:
    assert _mode_or_default("rule") == "rule"
    assert _mode_or_default("llm") == "llm"


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


def test_rule_mode_applies_document_rules_without_extra_punctuation_conversion(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    files, _ = optimize_generated_files_with_report([_doc_file()], [], mode="rule")
    docs = files[0].content["documents"]

    assert docs[1]["tts"]["text"] == "ドット ギットイグノア と ドット イーエヌブイ と ギット イニット を確認します。"


def test_rule_mode_omits_document_tts_when_reading_matches_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    files, _ = optimize_generated_files_with_report([_plain_doc_file()], [], mode="rule")
    doc = files[0].content["documents"][0]

    assert "tts" not in doc


def test_llm_mode_omits_document_tts_when_reading_matches_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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


def test_llm_prompt_keeps_original_punctuation_instruction() -> None:
    prompt = _tts_reading_prompt("確認します。", [])
    rules_block = _tts_reading_rules_block([])

    for text in [prompt, rules_block]:
        assert 'Keep the original Japanese punctuation as-is. Do not convert sentence-ending "。" to "、", and do not add or remove punctuation.' in text
        assert 'A period "." between digits or inside numbers/codes must stay as the source; do not convert it.' in text
        assert 'normalize sentence endings "。" and "." to "、"' not in text
        assert "〜します。 -> 〜します、" not in text


def test_quiz_tts_schema_keeps_answer_text_but_removes_choices_text() -> None:
    assert "answerText" in QuizTts.model_fields
    assert "choicesText" not in QuizTts.model_fields


def test_llm_mode_generates_kana_for_unknown_dot_words_and_keeps_core_rules(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
        return {"items": [{"id": "doc-1", "text": "シーアールエム의確認をします。"}]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([file], [TtsRule(source="CRM", reading="シーアールエム")], mode="llm")
    tts = files[0].content["documents"][0]["tts"]

    assert tts["text"] == "シーアールエムを確認します。"
    assert any(issue.issueType == "unexpected_script" and issue.field == "text" for issue in report.issues)


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

        def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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


def test_auto_mode_reruns_only_items_with_tts_report_issues(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
        calls.append(prompt)
        return {
            "items": [
                {
                    "id": "doc-1",
                    "text": "ドット ギットコンフィグ と ドット ギットログ を確認します、バージョン いってんに も確認します、",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([_doc_file()], [], mode="auto")
    docs = files[0].content["documents"]

    assert docs[0]["tts"]["text"] == "ドット ギットコンフィグ と ドット ギットログ を確認します、バージョン いってんに も確認します、"
    assert docs[1]["tts"]["text"] == "ドット ギットイグノア と ドット イーエヌブイ と ギット イニット を確認します。"
    assert len(calls) == 1
    assert report.issues == []
    assert report.llmGeneratedIds == ["doc-1"]


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


def test_plan_rules_still_override_llm_output(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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


def test_llm_quiz_uses_chunk_count_instead_of_question_count(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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


def test_llm_quiz_omits_choice_texts_when_choices_match_source(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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


def test_llm_quiz_outputs_choice_texts_and_preserves_meaningful_punctuation_and_tags(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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

    files, _ = optimize_generated_files_with_report([_plain_quiz_file()], [], mode="llm")
    tts = files[0].content["questions"][0]["tts"]

    assert tts["choiceTexts"] == ["[en-US]Save it", "OK.", "続けます?", "本当です？"]
    assert "choicesText" not in tts
    assert "questionText" not in tts
    assert tts["explanationText"] == "[en-US]Save it? [ja-JP]を選びます."
    assert tts.get("answerText") is None


def test_llm_quiz_falls_back_to_rules_when_batch_response_omits_question(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
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


def test_auto_quiz_reruns_only_question_with_report_issue(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")
    calls: list[str] = []

    def fake_generate_json(self, prompt: str, model: str | None = None) -> dict:
        calls.append(prompt)
        assert "- id: q-2" in prompt
        return {
            "items": [
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
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    files, report = optimize_generated_files_with_report([_quiz_file()], [], mode="auto")
    questions = files[0].content["questions"]

    assert len(calls) == 1
    assert "ドット ギットconfig" not in str(questions[1]["tts"])
    assert "ドット ギットコンフィグ" in questions[1]["tts"]["questionText"]
    assert report.issues == []
    assert report.llmGeneratedIds == ["q-2"]
