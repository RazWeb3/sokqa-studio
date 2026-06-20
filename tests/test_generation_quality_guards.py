import logging

from app.schemas.common import ReadingPattern
from app.schemas.request import GeneratePackRequest
from app.schemas.sokqa import CoursePlan, GeneratedFile, PlanDocument, PlanQuizPack, SokqaDocumentPack, SokqaQuizPack
from app.services import pack_agent
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
    plan.structurePolicy = "sequential"
    plan.materialMode = "strict"
    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "Structure policy: sequential" in prompt
        assert "Introduce terms only after their prerequisites" in prompt
        assert "Material mode: strict" in prompt
        assert "Do not add outside facts, terms, examples, claims, or inferred details" in prompt


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
    quiz_pack = plan.quizPacks[0]
    content = {
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
