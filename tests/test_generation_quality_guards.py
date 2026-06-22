import logging

from app.schemas.common import ReadingPattern
from app.schemas.request import GeneratePackRequest
from app.schemas.sokqa import CoursePlan, GeneratedFile, PlanDocument, PlanQuizPack, SokqaDocumentPack, SokqaQuizPack
from app.services import pack_agent
from app.services.document_generator import generate_mock_document_pack, normalize_document_content
from app.services.tagging import document_global_tags, quiz_global_tags
from app.services.pack_agent import generate_pack
from app.services.prompts import document_generation_prompt, quiz_generation_prompt
from app.services.quiz_generator import normalize_quiz_content
from app.services.repairer import QUIZ_REPAIR_INSTRUCTIONS, repair_files
from app.services.validator import validate_files


def _plan() -> CoursePlan:
    return CoursePlan(
        id="quality_pack",
        title="品質改善パック",
        description="生成品質を確認するパックです。",
        targetUser="学習者",
        difficulty="beginner",
        documents=[PlanDocument(id="doc_01", title="基礎", goal="基礎を理解する")],
        quizPacks=[
            PlanQuizPack(
                id="quiz_01",
                title="確認クイズ",
                purpose="key_concepts",
                questionCount=1,
                sourceDocumentIds=["doc_01"],
            )
        ],
    )


def _source_pack() -> SokqaDocumentPack:
    return SokqaDocumentPack(
        id="quality_pack_doc_01",
        title="基礎",
        documents=[{"id": "doc-1", "text": "安全なパスワード管理はアカウント保護に役立ちます。"}],
    )


def test_quiz_generation_prompt_requires_consistency_integer_and_direct_style() -> None:
    plan = _plan()
    quiz_pack = plan.quizPacks[0]

    prompt = quiz_generation_prompt(plan, quiz_pack, [_source_pack()])

    assert "single correct answer" in prompt
    assert "question, choices, answerIndex, and explanation are logically consistent" in prompt
    assert "Use 2, not \"2\"" in prompt
    assert "do not mention the source or documents" in prompt
    assert "ドキュメントによると" in prompt
    assert "推奨されています" in prompt


def test_generation_prompts_include_structure_and_material_policies() -> None:
    plan = _plan()
    plan.structurePolicy = "standard"
    plan.materialMode = "strict"
    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "Structure policy: standard" in prompt
        assert "balanced Sokqa course style" in prompt
        assert "Material mode: strict" in prompt
        assert "Do not add outside facts, terms, examples, claims, or inferred details" in prompt


def test_listening_document_prompt_forbids_glossary_style_and_requires_flow() -> None:
    plan = _plan()
    plan.structurePolicy = "listening"

    prompt = document_generation_prompt(plan, plan.documents[0])

    assert "Structure policy: listening" in prompt
    assert "continuous spoken narrative" in prompt
    assert "Do not write glossary-style entries" in prompt
    assert "not as an independent term definition" in prompt
    assert "avoid starting sections with a term name followed by its definition" in prompt
    assert "3 to 6 sentences in the pack language" in prompt


def test_standard_document_prompt_keeps_existing_balanced_structure() -> None:
    plan = _plan()
    plan.structurePolicy = "standard"

    prompt = document_generation_prompt(plan, plan.documents[0])

    assert "Structure policy: standard" in prompt
    assert "Use the existing balanced Sokqa course style" in prompt
    assert "Each text should be 2 to 4 sentences in the pack language" in prompt
    assert "avoid starting sections with a term name followed by its definition" not in prompt


def test_listening_mock_document_uses_connected_spoken_style() -> None:
    plan = _plan()
    plan.structurePolicy = "listening"
    plan.documents[0].keyPoints = ["最初の考え方", "次のつながり"]
    plan.documents[0].targetSectionCount = 2

    pack = generate_mock_document_pack(plan, plan.documents[0])

    assert "セクション1です" not in pack.documents[0].text
    assert "流れ" in pack.documents[0].text or "順番" in pack.documents[0].text
    assert "前の話を受けて" in pack.documents[1].text


def test_generated_global_tags_follow_language_content_and_limit() -> None:
    plan = _plan()
    plan.language = "ja"
    plan.shortTitle = "ITパスポート"
    plan.title = "ITパスポート 経営戦略パック"
    plan.globalTags = ["ITパスポート基礎", "ITパスポート試験"]
    plan.documents[0].title = "ITパスポート 1. 経営戦略とマーケティング"
    plan.documents[0].keyPoints = ["経営戦略", "マーケティング", "財務"]
    plan.quizPacks[0].title = "ITパスポート 理解チェック1（1章: 経営戦略）"

    doc_tags = document_global_tags(plan, plan.documents[0])
    quiz_tags = quiz_global_tags(plan, plan.quizPacks[0])

    assert len(doc_tags) <= 3
    assert len(quiz_tags) <= 3
    assert doc_tags == ["ITパスポート基礎", "ITパスポート試験"]
    assert quiz_tags == ["ITパスポート基礎", "ITパスポート試験"]
    assert "1." not in doc_tags
    assert "1章" not in quiz_tags
    assert "総合確認" not in quiz_tags


def test_generated_global_tags_use_latin_language_fallback() -> None:
    plan = _plan()
    plan.language = "en"
    plan.shortTitle = "Security"
    plan.title = "Security Strategy Pack"
    plan.globalTags = ["Security", "Strategy"]
    plan.documents[0].title = "Security Strategy and Risk"
    plan.documents[0].keyPoints = ["Risk management", "Incident response", "Governance"]

    tags = document_global_tags(plan, plan.documents[0])

    assert tags == ["Security", "Strategy"]
    assert all(tag for tag in tags)


def test_global_tags_drop_structural_words_and_do_not_pad() -> None:
    plan = _plan()
    plan.language = "ja"
    plan.shortTitle = "ITパスポート基礎"
    plan.title = "ITパスポート基礎 集中速習ガイド"
    plan.globalTags = ["ITパスポート基礎", "総合確認", "1章", "1.", "ビジネス"]
    plan.quizPacks[0].title = "ITパスポート基礎 総合確認（1章: ビジネス）"

    tags = quiz_global_tags(plan, plan.quizPacks[0])

    assert tags == ["ITパスポート基礎", "ビジネス"]


def test_global_tags_fallback_to_meaningful_theme_when_plan_tags_are_empty() -> None:
    plan = _plan()
    plan.language = "ja"
    plan.shortTitle = "ITパスポート試験"
    plan.title = "確認"
    plan.globalTags = ["1章", "総合確認"]
    plan.documents[0].title = "第1章"
    plan.documents[0].goal = "確認"
    plan.documents[0].keyPoints = ["章"]

    tags = document_global_tags(plan, plan.documents[0])

    assert tags == ["ITパスポート試験"]


def test_document_normalization_forces_unique_id_and_content_tags() -> None:
    plan = _plan()
    plan.contentId = "cnt_unique_doc"
    plan.shortTitle = "ITパスポート"
    plan.title = "ITパスポート 経営戦略パック"
    plan.globalTags = ["ITパスポート", "経営戦略"]
    plan.documents[0].title = "ITパスポート 1. 経営戦略"
    plan.documents[0].keyPoints = ["経営戦略", "マーケティング", "財務"]

    normalized = normalize_document_content(
        {
            "id": "old_doc_01",
            "title": "古いタイトル",
            "globalTags": ["it", "beginner", "extra", "too-many"],
            "documents": [{"id": "doc-1", "text": "本文です。"}],
        },
        plan,
        plan.documents[0],
    )

    assert normalized["id"] == "cnt_unique_doc_doc_01"
    assert len(normalized["globalTags"]) <= 3
    assert "it" not in normalized["globalTags"]
    assert normalized["globalTags"] == ["ITパスポート", "経営戦略"]


def test_quiz_prompt_uses_source_text_when_documents_are_absent() -> None:
    plan = _plan()
    plan.generationUnit = "quiz"
    plan.documents = []
    plan.sourceText = "資料では二要素認証と長いパスワードの併用を扱います。"
    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [])

    assert "Quiz context source: sourceText" in prompt
    assert "資料では二要素認証と長いパスワードの併用を扱います。" in prompt
    assert "Use this sourceText as the direct quiz context" in prompt


def test_quiz_prompt_prefers_generated_documents_over_source_text() -> None:
    plan = _plan()
    plan.sourceText = "この資料だけにあるバックアップ運用の話。"
    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    assert "Quiz context source: generated documents" in prompt
    assert "安全なパスワード管理はアカウント保護に役立ちます。" in prompt
    assert "この資料だけにあるバックアップ運用の話。" not in prompt


def test_quiz_prompt_generic_fallback_when_no_context_exists() -> None:
    plan = _plan()
    plan.documents = []
    plan.sourceText = None
    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [])

    assert "Quiz context source: generic fallback" in prompt
    assert "No generated documents or sourceText were provided" in prompt


def test_strict_quiz_only_source_text_prompt_forbids_outside_information() -> None:
    plan = _plan()
    plan.generationUnit = "quiz"
    plan.materialMode = "strict"
    plan.documents = []
    plan.sourceText = "資料にある用語だけで確認問題を作る。"
    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [])

    assert "Quiz context source: sourceText" in prompt
    assert "Use only this sourceText as quiz context" in prompt
    assert "Do not add outside facts, terms, examples, claims, or inferred details" in prompt


def test_selected_reading_patterns_are_injected_into_generation_prompts() -> None:
    plan = _plan()
    plan.ttsReadingMode = "llm"
    plan.proposedReadingPatterns = [
        ReadingPattern(
            id="dot_notation",
            title="ドット記法を読み下す",
            description=".config などのドットを含む表記を読み上げやすく扱う",
            examples=[".config -> ドット コンフィグ"],
            recommended=True,
        ),
        ReadingPattern(
            id="unused",
            title="未選択",
            description="この方針は選択されていません",
            examples=["unused"],
        ),
    ]
    plan.selectedReadingPatternIds = ["dot_notation"]

    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    assert "TTS reading hints selected by the user" in document_prompt
    assert "TTS reading hints selected by the user" in quiz_prompt
    assert "ドット記法を読み下す" in document_prompt
    assert ".config -> ドット コンフィグ" in quiz_prompt
    assert "未選択" not in document_prompt
    assert "for the later TTS optimization step only" in document_prompt
    assert "Preserve canonical written notation" in quiz_prompt
    assert "Align generated learner-facing text with these policies" not in document_prompt
    assert "Do not output tts fields here" in quiz_prompt


def test_generation_prompts_preserve_canonical_body_notation() -> None:
    plan = _plan()

    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    assert "Preserve canonical written notation in body text" in document_prompt
    assert "IT, ROE, .git, .env, GitHub" in document_prompt
    assert "Do not convert them to kana readings in text" in document_prompt
    assert "Do not add pronunciation-only parentheticals in body text" in document_prompt
    assert "Preserve canonical written notation in question, choices, and explanation" in quiz_prompt
    assert "Do not add pronunciation-only parentheticals in question, choices, or explanation" in quiz_prompt


def test_japanese_learning_beginner_prompt_limits_target_japanese() -> None:
    plan = _plan()
    plan.language = "id"
    plan.title = "日本語 N5 あいさつ入門"
    plan.difficulty = "beginner"

    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "Japanese-learning difficulty guidance" in prompt
        assert "avoid kanji in principle" in prompt
        assert "JLPT N5-level vocabulary" in prompt
        assert "じこしょうかい instead of 自己紹介" in prompt


def test_custom_instructions_are_injected_into_generation_prompts() -> None:
    plan = _plan()
    plan.customInstructions = "各章に短い会話例を1つ入れ、専門用語は避ける。"

    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "Additional user conditions:" in prompt
        assert "各章に短い会話例を1つ入れ、専門用語は避ける。" in prompt
        assert "Do not let these conditions override the required JSON schema" in prompt


def test_reading_policy_block_is_omitted_when_no_pattern_is_selected() -> None:
    plan = _plan()
    plan.proposedReadingPatterns = [
        ReadingPattern(
            id="dot_notation",
            title="ドット記法を読み下す",
            description=".config などのドットを含む表記を読み上げやすく扱う",
            examples=[".config -> ドット コンフィグ"],
        )
    ]

    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    assert "TTS reading hints selected by the user" not in prompt
    assert "ドット記法を読み下す" not in prompt


def test_reading_policy_block_is_omitted_outside_llm_mode() -> None:
    plan = _plan()
    plan.ttsReadingMode = "multilingual"
    plan.proposedReadingPatterns = [
        ReadingPattern(
            id="alphabet",
            title="英略語を読む",
            description="英略語をカタカナ読みで扱う",
            examples=["IT -> アイティー"],
        )
    ]
    plan.selectedReadingPatternIds = ["alphabet"]

    prompt = document_generation_prompt(plan, plan.documents[0])

    assert "TTS reading hints selected by the user" not in prompt
    assert "英略語を読む" not in prompt


def test_generate_pack_filters_unknown_selected_reading_patterns_without_touching_tts_rules(monkeypatch) -> None:
    plan = _plan()
    plan.proposedReadingPatterns = [
        ReadingPattern(
            id="alphabet",
            title="英略語を読む",
            description="英略語をカタカナ読みで扱う",
            examples=["API -> エーピーアイ"],
        )
    ]
    plan.selectedReadingPatternIds = ["alphabet", "missing"]

    captured = {}

    def fake_document_pack(current_plan, *_args, **_kwargs):
        captured["selectedReadingPatternIds"] = current_plan.selectedReadingPatternIds
        captured["ttsRules"] = current_plan.ttsRules
        return _source_pack()

    clean_quiz = SokqaQuizPack(
        id="quality_pack_quiz_01",
        title="確認クイズ",
        questions=[
            {
                "id": "q-1",
                "question": "安全なパスワード管理として適切なものはどれですか？",
                "choices": ["短い共通語を使う", "使い回す", "長く一意なものを使う", "保存しない"],
                "answerIndex": 2,
                "explanation": "長く一意なパスワードは推測されにくくなります。",
            }
        ],
    )
    monkeypatch.setattr(pack_agent, "generate_document_pack", fake_document_pack)
    monkeypatch.setattr(pack_agent, "generate_quiz_pack", lambda *_args, **_kwargs: clean_quiz)

    generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert captured["selectedReadingPatternIds"] == ["alphabet"]
    assert captured["ttsRules"] == []


def test_generate_pack_clears_selected_reading_patterns_outside_llm_mode(monkeypatch) -> None:
    plan = _plan()
    plan.ttsReadingMode = "multilingual"
    plan.proposedReadingPatterns = [
        ReadingPattern(
            id="alphabet",
            title="英略語を読む",
            description="英略語をカタカナ読みで扱う",
            examples=["API -> エーピーアイ"],
        )
    ]
    plan.selectedReadingPatternIds = ["alphabet"]

    captured = {}

    def fake_document_pack(current_plan, *_args, **_kwargs):
        captured["selectedReadingPatternIds"] = current_plan.selectedReadingPatternIds
        return _source_pack()

    clean_quiz = SokqaQuizPack(
        id="quality_pack_quiz_01",
        title="確認クイズ",
        questions=[
            {
                "id": "q-1",
                "question": "安全なパスワード管理として適切なものはどれですか？",
                "choices": ["短い共通語を使う", "使い回す", "長く一意なものを使う", "保存しない"],
                "answerIndex": 2,
                "explanation": "長く一意なパスワードは推測されにくくなります。",
            }
        ],
    )
    monkeypatch.setattr(pack_agent, "generate_document_pack", fake_document_pack)
    monkeypatch.setattr(pack_agent, "generate_quiz_pack", lambda *_args, **_kwargs: clean_quiz)

    generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert captured["selectedReadingPatternIds"] == []


def test_normalize_quiz_content_converts_string_answer_index_to_int() -> None:
    plan = _plan()
    plan.answerPositionMode = "auto"
    plan.contentId = "cnt_unique_quiz"
    plan.shortTitle = "ITパスポート"
    plan.title = "ITパスポート 経営戦略パック"
    quiz_pack = plan.quizPacks[0]
    content = {
        "id": "old_quiz_01",
        "globalTags": ["it", "beginner", "extra", "too-many"],
        "questions": [
            {
                "id": "q-1",
                "question": "安全なパスワード管理として適切なものはどれですか？",
                "choices": ["短い共通語を使う", "使い回す", "長く一意なものを使う", "保存しない"],
                "answerIndex": "2",
                "explanation": "長く一意なパスワードは推測されにくくなります。",
            }
        ]
    }

    normalized = normalize_quiz_content(content, plan, quiz_pack)

    assert normalized["questions"][0]["answerIndex"] == 2
    assert isinstance(normalized["questions"][0]["answerIndex"], int)
    assert normalized["id"] == "cnt_unique_quiz_quiz_01"
    assert len(normalized["globalTags"]) <= 3
    assert "it" not in normalized["globalTags"]


def test_normalize_quiz_content_balances_answer_positions() -> None:
    plan = _plan()
    plan.answerPositionMode = "balanced"
    quiz_pack = plan.quizPacks[0]
    content = {
        "questions": [
            {
                "id": "q-1",
                "question": "問1",
                "choices": ["誤りA", "誤りB", "正解1", "誤りC"],
                "answerIndex": 2,
                "explanation": "正解1が正しいためです。",
            },
            {
                "id": "q-2",
                "question": "問2",
                "choices": ["誤りA", "誤りB", "正解2", "誤りC"],
                "answerIndex": 2,
                "explanation": "正解2が正しいためです。",
            },
        ]
    }

    normalized = normalize_quiz_content(content, plan, quiz_pack)

    assert [question["answerIndex"] for question in normalized["questions"]] == [0, 1]
    assert normalized["questions"][0]["choices"][0] == "正解1"
    assert normalized["questions"][1]["choices"][1] == "正解2"


def test_quiz_validator_logs_citation_style_without_invalidating(caplog) -> None:
    file = GeneratedFile(
        name="quiz_01.json",
        kind="quiz",
        content={
            "id": "quality_pack_quiz_01",
            "title": "確認クイズ",
            "questions": [
                {
                    "id": "q-1",
                    "question": "安全なパスワード管理として適切なものはどれですか？",
                    "choices": ["短い共通語を使う", "使い回す", "長く一意なものを使う", "保存しない"],
                    "answerIndex": 2,
                    "explanation": "ドキュメントによると、長く一意なパスワードが推奨されています。",
                }
            ],
        },
    )

    with caplog.at_level(logging.WARNING, logger="app.services.validator"):
        result = validate_files([file])

    assert result.valid is True
    assert len(result.errors) == 1
    assert result.errors[0].severity == "warning"
    assert "quiz citation-style wording detected" in caplog.text
    assert "questions.0.explanation" in caplog.text


def test_quiz_validator_does_not_log_for_direct_style(caplog) -> None:
    file = GeneratedFile(
        name="quiz_01.json",
        kind="quiz",
        content={
            "id": "quality_pack_quiz_01",
            "title": "確認クイズ",
            "questions": [
                {
                    "id": "q-1",
                    "question": "安全なパスワード管理として適切なものはどれですか？",
                    "choices": ["短い共通語を使う", "使い回す", "長く一意なものを使う", "保存しない"],
                    "answerIndex": 2,
                    "explanation": "長く一意なパスワードは推測されにくく、アカウント保護に役立ちます。",
                }
            ],
        },
    )

    with caplog.at_level(logging.WARNING, logger="app.services.validator"):
        result = validate_files([file])

    assert result.valid is True
    assert "quiz citation-style wording detected" not in caplog.text


def test_quiz_repair_instructions_include_answer_index_consistency() -> None:
    assert "answerIndex points to the single correct choice" in QUIZ_REPAIR_INSTRUCTIONS
    assert "explanation explains the choice at answerIndex" in QUIZ_REPAIR_INSTRUCTIONS
    assert "citation/hearsay wording" in QUIZ_REPAIR_INSTRUCTIONS


def test_generation_repair_runs_for_citation_style_warning(monkeypatch) -> None:
    plan = _plan()
    plan.enableTtsOptimize = False
    bad_quiz = SokqaQuizPack(
        id="quality_pack_quiz_01",
        title="確認クイズ",
        questions=[
            {
                "id": "q-1",
                "question": "安全なパスワード管理として適切なものはどれですか？",
                "choices": ["短い共通語を使う", "使い回す", "長く一意なものを使う", "保存しない"],
                "answerIndex": 2,
                "explanation": "ドキュメントによると、長く一意なパスワードが推奨されています。",
            }
        ],
    )
    calls = {"repair": 0}

    def fake_repair(files):
        calls["repair"] += 1
        return repair_files(files)

    monkeypatch.setattr(pack_agent, "generate_document_pack", lambda *_args, **_kwargs: _source_pack())
    monkeypatch.setattr(pack_agent, "generate_quiz_pack", lambda *_args, **_kwargs: bad_quiz)
    monkeypatch.setattr(pack_agent, "repair_files", fake_repair)

    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert calls["repair"] == 1
    assert generated.validation.valid is True
    quiz_file = next(file for file in generated.files if file.kind == "quiz")
    explanation = quiz_file.content["questions"][0]["explanation"]
    assert "ドキュメントによると" not in explanation
    assert "推奨されています" not in explanation


def test_generation_repair_limit_keeps_generation_successful_when_warning_remains(monkeypatch, caplog) -> None:
    plan = _plan()
    plan.enableTtsOptimize = False
    bad_quiz = SokqaQuizPack(
        id="quality_pack_quiz_01",
        title="確認クイズ",
        questions=[
            {
                "id": "q-1",
                "question": "安全なパスワード管理として適切なものはどれですか？",
                "choices": ["短い共通語を使う", "使い回す", "長く一意なものを使う", "保存しない"],
                "answerIndex": 2,
                "explanation": "ドキュメントによると、長く一意なパスワードが推奨されています。",
            }
        ],
    )
    calls = {"repair": 0}

    def no_op_repair(files):
        calls["repair"] += 1
        return files

    monkeypatch.setattr(pack_agent, "generate_document_pack", lambda *_args, **_kwargs: _source_pack())
    monkeypatch.setattr(pack_agent, "generate_quiz_pack", lambda *_args, **_kwargs: bad_quiz)
    monkeypatch.setattr(pack_agent, "repair_files", no_op_repair)

    with caplog.at_level(logging.WARNING, logger="app.services.pack_agent"):
        generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert calls["repair"] == 1
    assert generated.validation.valid is True
    assert any(error.severity == "warning" for error in generated.validation.errors)
    assert "generation repair warning remains after repair" in caplog.text


def test_generation_does_not_repair_clean_quiz(monkeypatch) -> None:
    plan = _plan()
    plan.enableTtsOptimize = False
    clean_quiz = SokqaQuizPack(
        id="quality_pack_quiz_01",
        title="確認クイズ",
        questions=[
            {
                "id": "q-1",
                "question": "安全なパスワード管理として適切なものはどれですか？",
                "choices": ["短い共通語を使う", "使い回す", "長く一意なものを使う", "保存しない"],
                "answerIndex": 2,
                "explanation": "長く一意なパスワードは推測されにくく、アカウント保護に役立ちます。",
            }
        ],
    )
    calls = {"repair": 0}

    def fake_repair(files):
        calls["repair"] += 1
        return files

    monkeypatch.setattr(pack_agent, "generate_document_pack", lambda *_args, **_kwargs: _source_pack())
    monkeypatch.setattr(pack_agent, "generate_quiz_pack", lambda *_args, **_kwargs: clean_quiz)
    monkeypatch.setattr(pack_agent, "repair_files", fake_repair)

    generated = generate_pack(GeneratePackRequest(plan=plan, persist=False))

    assert calls["repair"] == 0
    assert generated.validation.valid is True
