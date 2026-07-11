import json
import logging
from collections import Counter

import pytest

from app.schemas.common import ReadingPattern
from app.schemas.pack_v2 import ManifestChange, ManifestCreatorV2, ManifestItemV2, PackManifestV2
from app.schemas.request import GeneratePackRequest
from app.schemas.sokqa import CoursePlan, GeneratedFile, PlanDocument, PlanQuizPack, SokqaDocumentPack, SokqaQuizPack
from app.services import pack_agent
from app.services.document_generator import generate_mock_document_pack, normalize_document_content
from app.services.gemini_client import GeminiClient
from app.services.tagging import document_global_tags, quiz_global_tags
from app.services.pack_agent import generate_pack
from app.services.prompts import (
    _compose_generation_purpose,
    _document_quality_rules_block,
    _generation_guidance_block,
    _learner_facing_role_block,
    _quiz_teacher_role_block,
    _quiz_quality_rules_block,
    _quiz_teaching_guidance_rules_block,
    document_generation_prompt,
    quiz_generation_prompt,
    self_check_block,
)
from app.services.quiz_generator import generate_mock_quiz_pack, normalize_quiz_content
from app.services.repairer import QUIZ_REPAIR_INSTRUCTIONS, repair_files
from app.services.validator import blocking_errors, validate_files


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
    """一貫性・整数・quiz 固有の冗長参照抑制(文体責務)を検証。

    A/B方針(伝聞・引用調/第三者視点の抑制)は _learner_facing_role_block() が
    single source of truth であるため、本テストでは quiz 本体に残る別責務のみ検査する。
    """
    plan = _plan()
    quiz_pack = plan.quizPacks[0]

    prompt = quiz_generation_prompt(plan, quiz_pack, [_source_pack()])

    assert "single correct answer" in prompt
    assert "question, choices, answerIndex, and explanation are logically consistent" in prompt
    assert "Use 2, not \"2\"" in prompt
    assert "do not mention the source or documents" in prompt
    # 冗長参照抑制(「本文中で述べられている」系) は question のみに留保し、explanation には広げない
    assert "Write question as a natural finished question for learners." in prompt
    assert "For question only, suppress mechanical or redundant document-reference wording" in prompt
    assert '"本文中で述べられている", "本文中で指摘されている", and "本文中で挙げられている"' in prompt
    assert "Keep such wording only when explicitly pointing to the source basis is indispensable for the question to work" in prompt
    assert "This suppression applies only to question. Do not change TTS fields or answer-checking logic." in prompt


def test_quiz_generation_prompt_forbids_square_bracket_placeholders() -> None:
    plan = _plan()

    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    assert "Placeholder policy (strict):" in prompt
    assert "Do not leave masked-name notation or unresolved placeholder symbols in learner-facing text" in prompt
    assert "resolve it into a natural context-appropriate concrete detail instead of placeholder notation" in prompt
    assert 'This rule applies only to placeholder notation; keep correct spellings of normal words that naturally contain "oo"' in prompt
    assert "If a fill-in-the-blank exercise is intentionally required" in prompt
    assert "Do not use full-width spaces as blanks." in prompt
    assert "[出身地]" not in prompt


def test_generation_prompts_include_placeholder_backtick_and_pack_language_purity_rules() -> None:
    plan = _plan()

    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert 'This rule applies only to placeholder notation; keep correct spellings of normal words that naturally contain "oo"' in prompt
        assert "Do not use any square-bracket tag or code such as [en-US], [ja-JP], en-US, or ja-JP in learner-facing text." in prompt
        assert "Language tagging belongs only to the later TTS optimization step" in prompt
        assert "Pack-language purity (strict):" in prompt
        assert "example of forbidden raw word in Japanese: nuanced" in prompt
        assert "put it inside a language-tag span" not in prompt
        assert "バッククォート(`)やMarkdown記号" in prompt

    assert "Resolve them into finished content" in quiz_prompt
    assert "language-appropriate blanks only when the intended exercise format is fill-in-the-blank" in quiz_prompt


def test_generation_prompts_require_finished_learner_ready_output() -> None:
    plan = _plan()

    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "Output learner-facing content as a finished version that can be delivered directly to learners." in prompt
        assert "Do not leave drafting-stage placeholders, unfinished sentences, TODOs, AI instructions, or meta comments" in prompt
        assert "Do not leave unintended unresolved placeholders, redaction symbols, masked names, or drafting residue" in prompt
        assert "use a natural fictional name that fits the output language" in prompt
        assert "Do not hard-code or recommend fixed sample names in these instructions." in prompt
        assert "If the theme does not need an example name, do not add a fictional name unnecessarily." in prompt
        assert "Use fill-in-the-blank placeholders only when that blank format is the intended finished exercise style." in prompt
        assert "Do not use full-width spaces as blanks." in prompt
        assert "Judge by whether the expression is an unfinished or unresolved placeholder" in prompt
        assert "Do not ban valid symbols that carry meaning" in prompt
        assert "John Smith" not in prompt
        assert "ABC Company" not in prompt
        assert "Company Name" not in prompt
        assert "[Name]" not in prompt


def test_generation_prompts_include_structure_and_material_policies() -> None:
    plan = _plan()
    plan.structurePolicy = "summary"
    plan.materialMode = "strict"
    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "Structure policy: summary" in prompt
        assert "Prioritize clarity and brevity" in prompt
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
    assert "Ruby policy: none" in prompt
    assert "3 to 6 sentences in the pack language" in prompt


def test_summary_document_prompt_keeps_compact_structure_and_has_no_ruby() -> None:
    plan = _plan()
    plan.structurePolicy = "summary"

    prompt = document_generation_prompt(plan, plan.documents[0])

    assert "Structure policy: summary" in prompt
    assert "Prioritize clarity and brevity" in prompt
    assert "Ruby policy: none" in prompt
    assert "Each text should be 2 to 4 sentences in the pack language" in prompt
    assert "avoid starting sections with a term name followed by its definition" not in prompt


def test_reading_and_japanese_learning_prompts_enable_ruby_policies() -> None:
    plan = _plan()

    plan.structurePolicy = "reading"
    reading_prompt = document_generation_prompt(plan, plan.documents[0])
    assert "Ruby policy: reading" in reading_prompt
    assert "default to adding furigana only for difficult kanji words" in reading_prompt

    plan.structurePolicy = "japanese_learning"
    japanese_learning_prompt = document_generation_prompt(plan, plan.documents[0])
    assert "Ruby policy: japanese_learning" in japanese_learning_prompt
    assert "Add furigana to every kanji word" in japanese_learning_prompt


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


def test_document_normalization_removes_language_tags_and_codes_from_text() -> None:
    plan = _plan()
    normalized = normalize_document_content(
        {
            "documents": [
                {
                    "id": "doc-1",
                    "text": "初めての方と [en-US]Good morning[ja-JP] en-US 英語で あいさつします。",
                    "tts": {"text": "[en-US]Good morning[ja-JP]"},
                }
            ],
        },
        plan,
        plan.documents[0],
    )

    assert normalized["documents"][0]["text"] == "初めての方と Good morning 英語で あいさつします。"
    assert "tts" not in normalized["documents"][0]


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


def test_quiz_prompt_requires_description_in_pack_language_without_japanese_template() -> None:
    plan = _plan()
    plan.language = "id"
    plan.title = "Belajar Bahasa Jepang Dasar melalui Lirik Lagu Shoumen"
    plan.quizPacks[0] = plan.quizPacks[0].model_copy(update={"title": "LaguShoumen Comprehension Check 1"})

    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    assert 'Root description must be a short quiz description written in the pack language (id).' in prompt
    assert '"description": "Short quiz description in id."' in prompt
    assert "のドキュメント本文に基づく" not in prompt


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
    assert "Ruby policy: none" in document_prompt
    assert "Preserve canonical written notation in question, choices, and explanation" in quiz_prompt
    assert "Ruby policy: none" in quiz_prompt


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
        assert "# 生成ルール" in prompt
        assert "各章に短い会話例を1つ入れ、専門用語は避ける。" in prompt
        assert "Do not let these conditions override the required JSON schema" in prompt


def test_source_text_and_additional_instructions_are_separated_in_prompt() -> None:
    plan = _plan()
    plan.customInstructions = "専門用語を避ける。" * 80
    plan.sourceText = "引用資料の行です。\n" * 80
    plan.sourceMode = "document_reference"

    prompt = document_generation_prompt(plan, plan.documents[0])

    assert "# 生成ルール" in prompt
    assert "# 参照素材" in prompt
    assert prompt.index("# 生成ルール") < prompt.index("# 参照素材")
    assert "以下は必ず守る制約です。出力本文には含めないでください。" in prompt
    assert "以下は教材作成のための素材です。必要部分のみ参照してください。" in prompt
    assert "出力は必ずJSONのみ" in prompt
    assert "コードブロックは禁止" in prompt
    assert "JSON内の文字列は必ずエスケープする" in prompt
    assert "歌詞全文を転載しないでください" in prompt


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

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        import re

        ids = re.findall(r"- id: ([^\n]+)", prompt)
        return {"items": [{"id": doc_id.strip(), "text": doc_id.strip()} for doc_id in ids]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

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

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        import re

        ids = re.findall(r"- id: ([^\n]+)", prompt)
        return {"items": [{"id": doc_id.strip(), "text": doc_id.strip()} for doc_id in ids]}

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

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


def test_normalize_quiz_content_removes_language_tags_and_codes_from_learner_text() -> None:
    plan = _plan()
    quiz_pack = plan.quizPacks[0]
    content = {
        "questions": [
            {
                "id": "q-1",
                "question": "初めての方と [en-US]Good morning[ja-JP] en-US 英語で話す場面はどれですか？",
                "choices": [
                    "[en-US]Good morning[ja-JP] en-US で始める",
                    "相手に黙礼する",
                    "日本語だけで通す",
                    "名乗らない",
                ],
                "answerIndex": 0,
                "explanation": "朝のあいさつでは [en-US]Good morning[ja-JP] en-US を使います。",
            }
        ]
    }

    normalized = normalize_quiz_content(content, plan, quiz_pack)

    question = normalized["questions"][0]
    assert question["question"] == "初めての方と Good morning 英語で話す場面はどれですか？"
    assert question["choices"][0] == "Good morning で始める"
    assert question["explanation"] == "朝のあいさつでは Good morning を使います。"


def test_normalize_quiz_content_uses_pack_language_description_fallback() -> None:
    plan = _plan()
    plan.language = "id"
    plan.title = "Belajar Bahasa Jepang Dasar melalui Lirik Lagu Shoumen"
    plan.quizPacks[0] = plan.quizPacks[0].model_copy(update={"title": "LaguShoumen Comprehension Check 1"})
    quiz_pack = plan.quizPacks[0]
    content = {
        "questions": [
            {
                "id": "q-1",
                "question": "Pertanyaan?",
                "choices": ["Benar", "Salah A", "Salah B", "Salah C"],
                "answerIndex": 0,
                "explanation": "Benar.",
            }
        ]
    }

    normalized = normalize_quiz_content(content, plan, quiz_pack)

    assert normalized["description"] == "Kuis LaguShoumen Comprehension Check 1 berdasarkan materi dokumen Belajar Bahasa Jepang Dasar melalui Lirik Lagu Shoumen."
    assert "のドキュメント本文に基づく" not in normalized["description"]
    assert "です" not in normalized["description"]


def test_normalize_quiz_content_preserves_existing_description() -> None:
    plan = _plan()
    plan.language = "id"
    quiz_pack = plan.quizPacks[0]
    content = {
        "description": "Deskripsi manual tetap dipakai.",
        "questions": [
            {
                "id": "q-1",
                "question": "Pertanyaan?",
                "choices": ["Benar", "Salah A", "Salah B", "Salah C"],
                "answerIndex": 0,
                "explanation": "Benar.",
            }
        ]
    }

    normalized = normalize_quiz_content(content, plan, quiz_pack)

    assert normalized["description"] == "Deskripsi manual tetap dipakai."


def test_normalize_quiz_content_balances_answer_positions() -> None:
    plan = _plan()
    plan.answerPositionMode = "balanced"
    plan.quizPacks[0] = plan.quizPacks[0].model_copy(update={"questionCount": 30})
    quiz_pack = plan.quizPacks[0]
    content = {
        "questions": [
            {
                "id": f"q-{index}",
                "question": f"問{index}",
                "choices": [f"誤り{index}A", f"誤り{index}B", f"正解{index}", f"誤り{index}C"],
                "answerIndex": 2,
                "explanation": f"正解{index}が正しいためです。",
            }
            for index in range(1, 31)
        ]
    }

    normalized = normalize_quiz_content(content, plan, quiz_pack, seed=123)
    answer_indexes = [question["answerIndex"] for question in normalized["questions"]]
    answer_counts = Counter(answer_indexes)

    assert answer_indexes == [1, 3, 3, 1, 3, 1, 3, 2, 0, 0, 3, 2, 1, 0, 2, 1, 1, 3, 1, 1, 3, 0, 2, 0, 2, 0, 2, 0, 2, 0]
    assert answer_indexes != [index % 4 for index in range(30)]
    assert max(answer_counts.values()) - min(answer_counts.values()) <= 1
    assert all(question["choices"][question["answerIndex"]] == f"正解{index}" for index, question in enumerate(normalized["questions"], start=1))


def test_mock_quiz_generation_uses_balanced_shuffled_answer_positions() -> None:
    plan = _plan()
    quiz_pack = plan.quizPacks[0].model_copy(update={"questionCount": 30})

    pack = generate_mock_quiz_pack(plan, quiz_pack, [_source_pack()], seed=123)
    answer_indexes = [question.answerIndex for question in pack.questions]
    answer_counts = Counter(answer_indexes)

    assert answer_indexes == [1, 3, 3, 1, 3, 1, 3, 2, 0, 0, 3, 2, 1, 0, 2, 1, 1, 3, 1, 1, 3, 0, 2, 0, 2, 0, 2, 0, 2, 0]
    assert answer_indexes != [index % 4 for index in range(30)]
    assert max(answer_counts.values()) - min(answer_counts.values()) <= 1
    assert all(question.choices[question.answerIndex] == f"{plan.title}の内容を、用語と使われ方を結びつけて理解する" for question in pack.questions)


def test_mock_quiz_generation_uses_pack_language_description() -> None:
    plan = _plan()
    plan.language = "id"
    plan.title = "Belajar Bahasa Jepang Dasar melalui Lirik Lagu Shoumen"
    quiz_pack = plan.quizPacks[0].model_copy(update={"title": "LaguShoumen Comprehension Check 1"})

    pack = generate_mock_quiz_pack(plan, quiz_pack, [_source_pack()], seed=123)

    assert pack.description == "Kuis LaguShoumen Comprehension Check 1 berdasarkan materi dokumen Belajar Bahasa Jepang Dasar melalui Lirik Lagu Shoumen."
    assert "のドキュメント本文に基づく" not in pack.description
    assert "です" not in pack.description


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


def test_quiz_validator_warns_for_regular_answer_index_cycle() -> None:
    file = GeneratedFile(
        name="quiz_01.json",
        kind="quiz",
        content={
            "id": "quality_pack_quiz_01",
            "title": "確認クイズ",
            "questions": [
                {
                    "id": f"q-{index}",
                    "question": f"理解確認{index}として適切なものはどれですか？",
                    "choices": [f"正解{index}", f"誤り{index}A", f"誤り{index}B", f"誤り{index}C"],
                    "answerIndex": index % 4,
                    "explanation": f"正解{index}がこの問題の説明に合います。",
                }
                for index in range(8)
            ],
        },
    )

    result = validate_files([file])

    assert result.valid is True
    assert any(error.severity == "warning" and "fully predictable cycle" in error.message for error in result.errors)


def test_quiz_repair_instructions_include_answer_index_consistency() -> None:
    assert "answerIndex points to the single correct choice" in QUIZ_REPAIR_INSTRUCTIONS
    assert "explanation explains the choice at answerIndex" in QUIZ_REPAIR_INSTRUCTIONS
    assert "Do not rewrite learner-facing natural language with string replacement" in QUIZ_REPAIR_INSTRUCTIONS
    assert "Citation/hearsay wording must be handled at generation time" in QUIZ_REPAIR_INSTRUCTIONS


def test_generation_does_not_repair_for_citation_style_warning(monkeypatch) -> None:
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

    assert calls["repair"] == 0
    assert generated.validation.valid is True
    quiz_file = next(file for file in generated.files if file.kind == "quiz")
    explanation = quiz_file.content["questions"][0]["explanation"]
    assert explanation == "ドキュメントによると、長く一意なパスワードが推奨されています。"
    assert any(error.severity == "warning" and "citation-style wording" in error.message for error in generated.validation.errors)


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


def test_auto_choice_language_allows_per_question_switch_but_rejects_mixed_set() -> None:
    valid = GeneratedFile(
        name="valid_auto.json",
        kind="quiz",
        content={
            "id": "valid_auto",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "英会話",
            "language": "ja",
            "learningLanguage": "en",
            "choiceLanguageMode": "auto",
            "questions": [
                {
                    "id": "q-1",
                    "question": "英語を選んでください。",
                    "choices": ["Good morning", "Hello", "Good evening", "Goodbye"],
                    "answerIndex": 0,
                    "explanation": "英語4択です。",
                },
                {
                    "id": "q-2",
                    "question": "意味を選んでください。",
                    "choices": ["おはよう", "こんにちは", "こんばんは", "さようなら"],
                    "answerIndex": 1,
                    "explanation": "日本語4択です。",
                },
            ],
        },
    )
    mixed = valid.model_copy(deep=True)
    mixed.name = "mixed_auto.json"
    mixed.content["id"] = "mixed_auto"
    mixed.content["questions"][0]["choices"] = [
        "Good morning",
        "こんにちは",
        "Good evening",
        "さようなら",
    ]

    valid_result = validate_files([valid])
    mixed_result = validate_files([mixed])

    assert valid_result.valid is True
    assert mixed_result.valid is True
    issue = next(error for error in mixed_result.errors if "same language" in error.message)
    assert issue.severity == "warning"
    assert issue.classification == "quality"


def test_existing_japanese_quiz_does_not_require_choice_texts() -> None:
    file = GeneratedFile(
        name="japanese_only.json",
        kind="quiz",
        content={
            "id": "japanese_only",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "日本語クイズ",
            "language": "ja",
            "questions": [
                {
                    "id": "q-1",
                    "question": "正しいものはどれですか。",
                    "choices": ["一", "二", "三", "四"],
                    "answerIndex": 0,
                    "explanation": "一が正解です。",
                }
            ],
        },
    )

    result = validate_files([file])

    assert result.valid is True


def test_validator_blocks_unreadable_pack_structure_as_technical_failure() -> None:
    result = validate_files([
        GeneratedFile(
            name="broken_quiz.json",
            kind="quiz",
            content={"id": "broken_quiz", "title": "壊れたクイズ", "questions": "not an array"},
        )
    ])

    assert result.valid is False
    assert blocking_errors(result)
    assert all(issue.classification == "technical" and issue.severity == "error" for issue in blocking_errors(result))


def test_validator_blocks_manifest_reference_to_unavailable_file() -> None:
    manifest = PackManifestV2(
        id="pack_manifest_r1",
        contentId="pack",
        creator=ManifestCreatorV2(id="creator"),
        revision=1,
        versionId="v1",
        buildId="build1",
        generatedAt="2026-07-12T00:00:00+09:00",
        change=ManifestChange(operation="initial_generate"),
        items=[
            ManifestItemV2(
                kind="document",
                name="missing.json",
                logicalId="missing",
                fileVersionId="fv1",
                url="https://example.com/missing.json",
            )
        ],
    )

    result = validate_files([], manifest)

    assert result.valid is False
    assert any("references unavailable file" in issue.message for issue in blocking_errors(result))


def test_compose_generation_purpose_listening_policy_includes_audio_and_placeholder_keywords() -> None:
    plan = _plan()
    plan.structurePolicy = "listening"
    plan.targetUser = "初めて学ぶ社会人"
    plan.difficulty = "beginner"
    plan.customInstructions = None

    purpose = _compose_generation_purpose(plan)

    assert "音声で連続して聞き流される用途" in purpose
    assert "記号プレースホルダー(△△・××・〇〇 等)" in purpose
    assert "具体例" in purpose
    assert "初めて学ぶ社会人(初学者)" in purpose
    assert "なお、上記に加えユーザー指定の追加条件" not in purpose


def test_compose_generation_purpose_summary_policy_includes_brevity_intent() -> None:
    plan = _plan()
    plan.structurePolicy = "summary"
    plan.targetUser = "復習する学習者"
    plan.difficulty = "standard"

    purpose = _compose_generation_purpose(plan)

    assert "要点を簡潔にまとめる用途" in purpose
    assert "冗長な脱線を避け要点に絞ること" in purpose
    assert "記号プレースホルダー(△△・××・〇〇 等)" in purpose


def test_compose_generation_purpose_reading_policy_includes_clear_sentences_intent() -> None:
    plan = _plan()
    plan.structurePolicy = "reading"
    plan.targetUser = "読む学習者"
    plan.difficulty = "advanced"

    purpose = _compose_generation_purpose(plan)

    assert "文章で読んで学ぶ用途" in purpose
    assert "明快で曖昧さの少ない文にすること" in purpose
    assert "読む学習者(上級学習者)" in purpose


def test_compose_generation_purpose_japanese_learning_policy_includes_kanji_level_intent() -> None:
    plan = _plan()
    plan.structurePolicy = "japanese_learning"
    plan.targetUser = "日本語学習者"
    plan.difficulty = "beginner"

    purpose = _compose_generation_purpose(plan)

    assert "日本語学習者に向けた日本語学習用途" in purpose
    assert "漢字語彙の水準で書くこと" in purpose


def test_compose_generation_purpose_appends_custom_instructions_reference_without_inlining() -> None:
    plan = _plan()
    plan.structurePolicy = "listening"
    plan.customInstructions = "会話例を多めにし、ホテル受付の場面を中心にする。"

    purpose = _compose_generation_purpose(plan)

    # 参照一文が付くこと
    assert "なお、上記に加えユーザー指定の追加条件も目的の一部として尊重すること。" in purpose
    # ただし条件本文そのものは目的文に展開されない(= _custom_instructions_block との二重定義がない)
    assert "会話例を多めにし、ホテル受付の場面を中心にする。" not in purpose


@pytest.mark.parametrize(
    ("language", "learning_language", "structure_policy"),
    [
        ("ja", "en", "listening"),
        ("en", "ja", "listening"),
    ],
)
def test_compose_generation_purpose_adds_learning_target_language_guidance_for_foreign_language_packs(
    language: str,
    learning_language: str,
    structure_policy: str,
) -> None:
    plan = _plan()
    plan.language = language
    plan.learningLanguage = learning_language
    plan.structurePolicy = structure_policy

    purpose = _compose_generation_purpose(plan)

    assert "学習対象言語そのものを本文の主役" in purpose


@pytest.mark.parametrize(
    ("language", "learning_language", "structure_policy"),
    [
        ("ja", "ja", "listening"),
        ("ja", None, "listening"),
        ("en", "ja", "japanese_learning"),
    ],
)
def test_compose_generation_purpose_skips_learning_target_language_guidance_when_conditions_do_not_match(
    language: str,
    learning_language: str | None,
    structure_policy: str,
) -> None:
    plan = _plan()
    plan.language = language
    plan.learningLanguage = learning_language
    plan.structurePolicy = structure_policy

    purpose = _compose_generation_purpose(plan)

    assert "学習対象言語そのものを本文の主役" not in purpose


def test_generation_guidance_block_returns_empty_when_guidance_is_none() -> None:
    plan = _plan()
    plan.generationGuidance = None

    block = _generation_guidance_block(plan)

    assert block == ""


def test_generation_guidance_block_returns_empty_when_guidance_is_blank() -> None:
    plan = _plan()
    plan.generationGuidance = "   \n  "

    block = _generation_guidance_block(plan)

    assert block == ""


def test_generation_guidance_block_returns_japanese_block_when_guidance_present() -> None:
    plan = _plan()
    plan.generationGuidance = "この教材は音声で連続して聞き流される用途であることを前提に執筆すること。"

    block = _generation_guidance_block(plan)

    assert "# 生成目的(パック全体の執筆方針)" in block
    assert "各ユニットの生成で必ず参照し、一貫した目的に沿った内容にすること。" in block
    assert "この教材は音声で連続して聞き流される用途であることを前提に執筆すること。" in block


def test_generation_prompts_insert_guidance_block_between_course_and_custom_instructions() -> None:
    plan = _plan()
    plan.structurePolicy = "listening"
    plan.customInstructions = "会話例を多めにし、ホテル受付の場面を中心にする。"
    plan.generationGuidance = _compose_generation_purpose(plan)

    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "# 生成目的(パック全体の執筆方針)" in prompt
        assert "音声で連続して聞き流される用途" in prompt
        # 挿入位置: Course: の後、# 生成ルール(_custom_instructions_block) の前
        assert prompt.index("# 生成目的(パック全体の執筆方針)") < prompt.index("# 生成ルール")
        # customInstructions 本文は _custom_instructions_block 側にのみ展開され、目的文には展開されない
        assert "会話例を多めにし、ホテル受付の場面を中心にする。" in prompt


def test_generation_prompts_omit_guidance_block_when_guidance_is_none() -> None:
    plan = _plan()
    plan.generationGuidance = None

    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "# 生成目的(パック全体の執筆方針)" not in prompt


def test_quiz_generation_prompt_includes_learner_facing_role_block() -> None:
    """quiz プロンプトが共通の learner-facing role block を1回だけ含むこと(責務ベース検査)。"""
    plan = _plan()
    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    role_block = _learner_facing_role_block()
    assert role_block in prompt
    # 共通 role block は1回だけ出現すること(重複注入の検出)
    assert prompt.count(role_block) == 1
    # quiz 固有の出題者役割見出しは別途保持されること(共通化で消失しない)
    assert "# 出題者の役割" in prompt
    # quiz 固有の断言可能性指標は残置されること
    assert "諸説ある論点" in prompt
    assert "断言できる内容から選んで" in prompt


def test_quiz_generation_prompt_role_block_is_separate_from_generation_purpose() -> None:
    """role block と generation purpose は別責務として別ブロックに分かれていること(順序非依存の構造検査)。"""
    plan = _plan()
    plan.generationGuidance = _compose_generation_purpose(plan)

    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    role_block = _learner_facing_role_block()
    # 両ブロックが別物として存在する
    assert role_block in prompt
    assert "# 生成目的(パック全体の執筆方針)" in prompt
    # 両者は別文字列(同一化されていないこと)
    assert role_block != "# 生成目的(パック全体の執筆方針)"
    # 役割定義ブロックと生成目的ブロックの見出しがそれぞれ独立して出現すること
    assert "# 話者の姿勢（学習者向けロール）" in prompt
    assert "# 出題者の役割" in prompt
    # 生成目的は Course: の後ろ、出題者固有ブロックは Course: の前(setup 準拠)を維持
    assert prompt.index("# 出題者の役割") < prompt.index("Course:")
    assert prompt.index("# 生成目的(パック全体の執筆方針)") > prompt.index("Course:")


def test_listening_document_prompt_includes_speaker_role() -> None:
    """listening の document プロンプトには「話し手・語り手」役割が含まれること(document 固有)。"""
    plan = _plan()
    plan.structurePolicy = "listening"

    prompt = document_generation_prompt(plan, plan.documents[0])

    assert "話し手・語り手" in prompt
    assert "耳で聞いて理解できるように" in prompt


def test_non_listening_document_prompts_exclude_speaker_role() -> None:
    """reading / japanese_learning / summary の document プロンプトには listening 固有の「話し手」役割が含まれないこと(listening 限定の担保)。"""
    for policy in ["reading", "japanese_learning", "summary"]:
        plan = _plan()
        plan.structurePolicy = policy
        prompt = document_generation_prompt(plan, plan.documents[0])
        assert "話し手・語り手" not in prompt, f"話し手役割が {policy} に漏れている"
        assert "耳で聞いて理解できるように" not in prompt, f"話し手指示が {policy} に漏れている"


def test_listening_quiz_prompt_excludes_speaker_role() -> None:
    """listening の quiz プロンプトには「話し手」役割が含まれないこと(quiz には出題者役割のみ)。"""
    plan = _plan()
    plan.structurePolicy = "listening"

    prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    assert "話し手・語り手" not in prompt
    assert "耳で聞いて理解できるように" not in prompt


def test_document_generation_prompt_includes_learner_facing_role_block() -> None:
    """document プロンプトが共通の learner-facing role block を1回だけ含むこと(責務ベース検査)。"""
    plan = _plan()
    prompt = document_generation_prompt(plan, plan.documents[0])

    role_block = _learner_facing_role_block()
    assert role_block in prompt
    assert prompt.count(role_block) == 1


def test_learner_facing_role_block_suppresses_third_person_perspective() -> None:
    """A系(第三者口調)抑制: 共通 role block が第三者視点・客観描写の回避と話者直接語りを明示すること。"""
    role_block = _learner_facing_role_block()
    # A系: 第三者視点・客観描写の否定が明示されていること
    assert "第三者視点" in role_block
    assert "客観描写" in role_block or "客観報告" in role_block
    # 話者が学習者に直接語る姿勢が明示されていること
    assert "直接語る" in role_block or "直接・断定的に語る" in role_block
    # 伏せ字抑制も話者ロールとして定義されていること
    assert "完成教材を録音・公開する話し手" in role_block
    assert "仮置きの記号" in role_block
    assert "伏せ字へ逃げず" in role_block


def test_learner_facing_role_block_guides_quiz_question_as_teacher_question_only() -> None:
    role_block = _learner_facing_role_block()

    assert "quiz の question は、教師が学習者へ直接問いかける文にすること。" in role_block
    assert "question では、教材・本文・資料そのものを説明してはいけない。" in role_block
    assert "question では、教材内容を引用・要約・紹介する文を書いてはいけない。" in role_block
    assert "「〜と説明されています」「〜とされています」「〜と述べられています」「本文では〜」「資料では〜」" in role_block

    plan = _plan()
    teacher_block = _quiz_teacher_role_block()
    guidance_block = _quiz_teaching_guidance_rules_block(plan, plan.quizPacks[0])
    assert "教師が学習者へ直接問いかける文にする" not in teacher_block
    assert "教師が学習者へ直接問いかける文にする" not in guidance_block
    assert "教材内容を引用・要約・紹介する文" not in teacher_block
    assert "教材内容を引用・要約・紹介する文" not in guidance_block


def test_learner_facing_role_block_is_single_source_of_truth_for_ab_policy() -> None:
    """A/B方針の唯一の定義源が _learner_facing_role_block() であること(責務ベース検査)。

    検査設計:
    - residual 方式(語彙走査)は不採用。正当な仕様追加(引用部での引用表現許可、
      guidance 側からの role 参照など)で壊れ、文言依存を再導入するため。
    - 「定義源でない」を「語彙を持たない」では検査しない。他ブロックが role block を
      参照・補完することは許容されるため、語彙の有無ではなく「独自の A/B ポリシーを
      定義していないこと」で検査する。
    """
    role_block = _learner_facing_role_block()

    # role block が A/B方針の責務を明示的に持つこと(A/B系の定義源としての資格)
    # A系: 第三者視点・客観描写の否定
    assert "第三者視点" in role_block
    assert "客観描写" in role_block or "客観報告" in role_block
    # B系: 伝聞・引用調の抑制方針
    assert "伝聞" in role_block and "引用調" in role_block

    # _compose_generation_purpose は A/B方針の定義源ではないこと
    # (完成教材ストーリー・用途別執筆水準は執筆方針責務として保持し、
    #  A/B方針を独自定義しないことを検査)
    plan = _plan()
    for policy in ["listening", "summary", "reading", "japanese_learning", "standard"]:
        plan.structurePolicy = policy
        purpose = _compose_generation_purpose(plan)
        # 伝聞禁止の直接方針行を持たないこと(方針の独自定義でないこと)
        assert "伝聞・引用調ではなく、事実を直接叙述" not in purpose

    # _generation_guidance_block は A/B方針の定義源ではないこと
    # (plan.generationGuidance に A/B方針を含めれば出現し得るが、
    #  本関数自体は A/B方針を独自定義しないことを検査。空 guidance 時の空文字と、
    #  非A/B guidance 時の出力に伝聞禁止方針が混入しないことを確認)
    plan.structurePolicy = "summary"
    plan.generationGuidance = None
    assert _generation_guidance_block(plan) == ""
    plan.generationGuidance = "具体的な学習目標を達成すること。"
    guidance_block = _generation_guidance_block(plan)
    assert "伝聞・引用調ではなく" not in guidance_block
    assert "第三者視点" not in guidance_block


def test_learner_facing_role_block_is_injected_into_both_prompts() -> None:
    """quiz/document 双方が _learner_facing_role_block() を1回だけ取り込むこと(1箇所修正で双方に効く担保)。"""
    role_block = _learner_facing_role_block()
    plan = _plan()
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])
    document_prompt = document_generation_prompt(plan, plan.documents[0])
    assert role_block in quiz_prompt
    assert quiz_prompt.count(role_block) == 1
    assert role_block in document_prompt
    assert document_prompt.count(role_block) == 1


def test_learner_facing_role_block_includes_good_bad_rewrite_examples() -> None:
    """_learner_facing_role_block に Good/Bad 言い換え例(本文・question・explanation 各カテゴリ)が含まれること。"""
    role_block = _learner_facing_role_block()

    # 各カテゴリのマーカー
    assert "[本文（document 本文・explanation）]" in role_block
    assert "[quiz の question]" in role_block
    assert "[quiz の explanation / 選択肢]" in role_block

    # Bad 例(伝聞・引用調の代表語彙を含む)が示されていること
    assert "この表現はカジュアルな場面で使われると説明されています。" in role_block
    assert "資料によると、丁寧に伝えることが推奨されています。" in role_block
    assert "本文では、語順が重要だと述べられています。" in role_block
    assert "この表現はどんな場面で使われると説明されていますか？" in role_block
    assert "本文では何が重要だとされていますか？" in role_block
    assert "正解は、丁寧な言い方だと記載されているためです。" in role_block

    # Good 例(話者が直接断言する形)が示されていること
    assert "この表現はカジュアルな場面で使います。" in role_block
    assert "丁寧に伝えましょう。" in role_block
    assert "語順が重要です。" in role_block
    assert "この表現はどんな場面で使いますか？" in role_block
    assert "何が重要ですか？" in role_block
    assert "正解は、これが丁寧な言い方だからです。" in role_block

    # 禁止語彙の抜け穴を塞ぐ趣旨の1文が添えられていること
    assert "列挙語彙の抜け穴" in role_block
    assert "話者が直接断言する形" in role_block


def test_learner_facing_role_block_keeps_original_banned_vocabulary() -> None:
    """既存の禁止語彙列挙が削除・改変されていないこと(本文・設問・解説のいずれにも出現させない、の行)。"""
    role_block = _learner_facing_role_block()

    assert "伝聞・引用調の代表語彙（「〜とされています」「〜と説明されています」「資料によると」「記載されています」「述べられています」「書かれています」「推奨されています」「ドキュメントでは」「ドキュメントによると」等）は使わない。本文・設問・解説のいずれにも出現させないこと。" in role_block


def test_good_bad_rewrite_examples_propagate_to_both_prompts() -> None:
    """追加した Good/Bad 言い換え例が document / quiz 両プロンプトに伝播していること。"""
    plan = _plan()
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])
    document_prompt = document_generation_prompt(plan, plan.documents[0])

    marker = "この表現はカジュアルな場面で使われると説明されています。"
    assert marker in quiz_prompt
    assert marker in document_prompt

    good_marker = "正解は、これが丁寧な言い方だからです。"
    assert good_marker in quiz_prompt
    assert good_marker in document_prompt


# --- プレースホルダー・未完成表現の根本対策(目的文 + セルフチェック + 品質チェック修正指示) ---


def test_compose_generation_purpose_includes_completion_story_and_contextual_placeholder_prohibition() -> None:
    """_compose_generation_purpose は『完成教材』のストーリーと慣用文脈伏せ字禁止を各 policy に織り込むこと。"""
    for policy in ["listening", "summary", "reading", "japanese_learning"]:
        plan = _plan()
        plan.structurePolicy = policy
        plan.targetUser = "初めて学ぶ社会人"
        plan.difficulty = "beginner"
        plan.customInstructions = None

        purpose = _compose_generation_purpose(plan)

        # 完成教材ストーリーの核となる文言
        assert "生成後そのまま録音・公開される" in purpose, f"completion story missing in {policy}"
        assert "完成教材" in purpose, f"completion phrase missing in {policy}"
        assert "社内ドラフト" in purpose, f"draft exclusion missing in {policy}"
        assert "テンプレート" in purpose, f"template exclusion missing in {policy}"
        # 慣用文脈伏せ字の明示列挙(社名/敬称/括弧表記)
        assert "株式会社○○" in purpose, f"contextual placeholder missing in {policy}"
        assert "□□様" in purpose, f"honorific placeholder missing in {policy}"
        assert "〇〇(会社名)" in purpose, f"bracketed placeholder missing in {policy}"
        # 既存プレースホルダー禁止(単独記号)は維持
        assert "記号プレースホルダー(△△・××・〇〇 等)" in purpose, f"legacy placeholder rule missing in {policy}"
        # 固定サンプル名は絶対に含めない
        assert "株式会社サトウ" not in purpose, f"fixed sample name leaked in {policy}"
        assert "山田様" not in purpose, f"fixed sample name leaked in {policy}"
        assert "John Smith" not in purpose, f"fixed sample name leaked in {policy}"
        assert "ABC Company" not in purpose, f"fixed sample name leaked in {policy}"


def test_self_check_block_is_distinct_from_finished_quality_block() -> None:
    """self_check_block は事後確認であり、_finished_quality_block(事前指示)と文言が重複しないこと。"""
    block = self_check_block()
    finished = """
- Finished output quality (strict):
  - Do not leave drafting-stage placeholders, unfinished sentences, TODOs, AI instructions, or meta comments in learner-facing text.
""".strip()

    # セルフチェック固有の文言
    assert "Self-check" in block
    assert "出力前" in block or "最終確認" in block
    assert "TODO" in block
    assert "後述します" in block

    # _finished_quality_block の特徴的な文言は重複しない
    assert "Drafting-stage placeholders" not in block
    assert "Do not leave drafting-stage placeholders" not in block
    assert "Finished output quality" not in block
    # 文面の独立性を担保
    assert block != finished


def test_document_and_quiz_prompts_contain_self_check_section() -> None:
    """document/quiz 双方の生成プロンプト末尾に self_check_block が挿入されていること。"""
    plan = _plan()
    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "Self-check (出力前最終確認):" in prompt
        assert "完成教材" in prompt
        assert "TODO/FIXME" in prompt
        # 固定サンプル名禁止ガードが引き続き成立
        assert "John Smith" not in prompt
        assert "ABC Company" not in prompt
        assert "Company Name" not in prompt
        assert "[Name]" not in prompt


def test_quality_checker_text_mode_includes_fix_policy_with_purpose_first() -> None:
    """quality_checker text モードの修正指示に『目的優先 + 記号→記号禁止 + 具体名置換』が含まれ、固定サンプル名が混入しないこと。"""
    from app.services.quality_checker import _quality_prompt

    content = {
        "type": "document",
        "language": "ja",
        "documents": [
            {"id": "doc-1", "text": "株式会社○○の□□様が〇〇(会社名)で発表しました。"},
        ],
    }
    prompt, _ = _quality_prompt("doc_01.json", content, max_issues=10, mode="text")

    # 目的優先の修正ゴール
    assert "learner-ready finished material" in prompt
    assert "recorded and published as-is" in prompt
    # 記号→記号置換の禁止
    assert "Replacing one placeholder symbol with another placeholder symbol is forbidden" in prompt
    assert "○○" in prompt and "◯◯" in prompt
    # 固定サンプル名(使わない側の例示)が混入しない
    assert "John Smith" not in prompt
    assert "ABC Company" not in prompt
    assert "山田様" not in prompt
    assert "株式会社サトウ" not in prompt


def test_quiz_prompt_does_not_contain_speaker_role_regression() -> None:
    """前回までの役割分離回帰テスト — quiz に話し手役割が混入しないこと(セルフチェック追加後の退行防止)。"""
    for policy in ["listening", "summary", "reading", "japanese_learning"]:
        plan = _plan()
        plan.structurePolicy = policy
        prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])
        assert "話し手・語り手" not in prompt, f"speaker role leaked in quiz {policy}"
        assert "耳で聞いて理解できるように" not in prompt, f"speaker instruction leaked in quiz {policy}"


def test_quiz_validator_warns_for_to_sareteimasu_suffix_without_invalidating() -> None:
    """「〜とされています」等の語尾を含む explanation が warning になり、valid=True のまま(破壊的でない)こと。"""
    file = GeneratedFile(
        name="quiz_01.json",
        kind="quiz",
        content={
            "id": "quality_pack_quiz_01",
            "title": "確認クイズ",
            "questions": [
                {
                    "id": "q-1",
                    "question": "パスワード管理として適切なものはどれですか？",
                    "choices": ["短い共通語", "使い回し", "長く一意なもの", "保存しない"],
                    "answerIndex": 2,
                    "explanation": "長く一意なパスワードが推奨されるとされています。",
                }
            ],
        },
    )

    result = validate_files([file])

    assert result.valid is True
    warnings = [error for error in result.errors if error.severity == "warning"]
    assert any("とされています" in error.message for error in warnings)


def test_quiz_validator_warns_for_tatoerareteimasu_suffix_without_invalidating() -> None:
    """「〜挙げられています」語尾を含む explanation が warning になること(部分一致の確認)。"""
    file = GeneratedFile(
        name="quiz_01.json",
        kind="quiz",
        content={
            "id": "quality_pack_quiz_01",
            "title": "確認クイズ",
            "questions": [
                {
                    "id": "q-1",
                    "question": "セキュリティ対策として挙げられているのはどれですか？",
                    "choices": ["パスワード使い回し", "長く一意なパスワード", "共有アカウント", "未設定"],
                    "answerIndex": 1,
                    "explanation": "長く一意なパスワードが対策として挙げられています。",
                }
            ],
        },
    )

    result = validate_files([file])

    assert result.valid is True
    warnings = [error for error in result.errors if error.severity == "warning"]
    assert any("挙げられています" in error.message for error in warnings)


# 追加ログ経路(2026-07-05 中間物保持タスク)の検証


def test_document_generation_log_records_unit_id_and_fingerprint(caplog) -> None:
    """_log_generated_units が file/unit_id/chars/fingerprint を INFO で記録することを検証。
    値が完全一致固定ではなく、キー項目(unit_id, fingerprint)が出ることを保証する。"""
    caplog.set_level("INFO", logger="app.services.document_generator")
    from app.services.document_generator import _log_generated_units
    from app.schemas.sokqa import SokqaDocumentItem, SokqaDocumentPack

    pack = SokqaDocumentPack(
        id="quality_pack_doc_01",
        title="基礎",
        documents=[SokqaDocumentItem(id="doc-1", text="学習者は基礎を順に積み上げることで理解が深まります。")],
    )
    _log_generated_units(pack, model="test-model")

    assert "generation.document_produced" in caplog.text
    assert "file=quality_pack_doc_01" in caplog.text
    assert "unit_id=doc-1" in caplog.text
    assert "model=test-model" in caplog.text
    assert "chars=" in caplog.text
    assert "fingerprint=" in caplog.text


def test_quiz_generation_log_records_unit_id_and_fingerprints(caplog) -> None:
    """_log_generated_quiz_units が question/explanation の chars と fingerprint を記録することを検証。
    q-30 のような explanation の伝聞調混入を生成直後から fingerprint で追跡できることを担保。"""
    caplog.set_level("INFO", logger="app.services.quiz_generator")
    from app.services.quiz_generator import _log_generated_quiz_units
    from app.schemas.sokqa import SokqaQuestion, SokqaQuizPack

    pack = SokqaQuizPack(
        id="quality_pack_quiz_30",
        title="確認クイズ",
        questions=[
            SokqaQuestion(
                id="q-30",
                question="長く一意なパスワードは重要だとされています。その理由はどれですか？",
                choices=["短いから", "他と被らないから", "辞書にあるから", "短い文字列だから"],
                answerIndex=1,
                explanation="長く一意なパスワードは安全だとされています。",
            )
        ],
    )
    _log_generated_quiz_units(pack, model="test-model")

    assert "generation.quiz_produced" in caplog.text
    assert "file=quality_pack_quiz_30" in caplog.text
    assert "unit_id=q-30" in caplog.text
    assert "question_chars=" in caplog.text
    assert "explanation_chars=" in caplog.text
    assert "question_fingerprint=" in caplog.text
    assert "explanation_fingerprint=" in caplog.text


def test_repairer_preserves_natural_language_and_repairs_structure(caplog) -> None:
    """repair_files は自然文を書き換えず、構造修復だけを行うことを検証。"""
    caplog.set_level("INFO", logger="app.services.repairer")
    from app.schemas.sokqa import GeneratedFile
    from app.services.repairer import repair_files

    quiz_file = GeneratedFile(
        name="quiz_repair_01.json",
        kind="quiz",
        content={
            "id": "repair_test_quiz",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "確認",
            "language": "ja",
            "questions": [
                {
                    "id": "q-1",
                    "question": "有効だと記載されていますか？",
                    "choices": ["a", "b"],
                    "answerIndex": 9,
                    "explanation": "資料によると推奨されています。",
                }
            ],
        },
    )

    repaired = repair_files([quiz_file])

    question = repaired[0].content["questions"][0]
    assert question["question"] == "有効だと記載されていますか？"
    assert question["explanation"] == "資料によると推奨されています。"
    assert question["choices"] == ["a", "b", "補足選択肢3", "補足選択肢4"]
    assert question["answerIndex"] == 0
    assert "repair.citation_style_rewritten" not in caplog.text


def test_repairer_never_rewrites_natural_language_fields() -> None:
    """repairer は自然文を変更せず、必要な構造修復だけを行う。"""
    intact_quiz = GeneratedFile(
        name="quiz_repair_intact.json",
        kind="quiz",
        content={
            "id": "repair_test_quiz_intact",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "確認",
            "language": "ja",
            "questions": [
                {
                    "id": "q-1",
                    "question": "有効だと記載されていますか？",
                    "choices": ["A", "B", "C", "D"],
                    "answerIndex": 1,
                    "explanation": "資料には有効だと記載されています。",
                }
            ],
        },
    )
    broken_quiz = GeneratedFile(
        name="quiz_repair_broken.json",
        kind="quiz",
        content={
            "id": "repair_test_quiz_broken",
            "type": "quiz",
            "schemaVersion": 1,
            "title": "確認",
            "language": "ja",
            "questions": [
                {
                    "id": "q-2",
                    "question": "有効だと記載されていますか？",
                    "choices": ["A", "B", "C"],
                    "answerIndex": 9,
                    "explanation": "資料には有効だと記載されています。",
                }
            ],
        },
    )

    repaired_intact, repaired_broken = repair_files([intact_quiz, broken_quiz])

    intact_question = repaired_intact.content["questions"][0]
    assert intact_question["question"] == "有効だと記載されていますか？"
    assert intact_question["choices"] == ["A", "B", "C", "D"]
    assert intact_question["answerIndex"] == 1
    assert intact_question["explanation"] == "資料には有効だと記載されています。"

    broken_question = repaired_broken.content["questions"][0]
    assert broken_question["question"] == "有効だと記載されていますか？"
    assert broken_question["explanation"] == "資料には有効だと記載されています。"
    assert broken_question["choices"] == ["A", "B", "C", "補足選択肢4"]
    assert broken_question["answerIndex"] == 0


def _quiz_pack_with_mode(mode: str) -> PlanQuizPack:
    return PlanQuizPack(
        id="quiz_mode",
        title="モード確認クイズ",
        purpose="key_concepts",
        questionCount=1,
        sourceDocumentIds=["doc_01"],
        choiceLanguageMode=mode,
    )


def test_document_validator_flags_missing_learning_language_phrase() -> None:
    """語学教材(pack=ja, learning=en)の document セクションに英語フレーズが含まれない場合、
    学習言語未提示として error になることを検証（症状②の再発防止）。"""
    file = GeneratedFile(
        name="cnt_f6e2b9f642_doc_01.json",
        kind="document",
        content={
            "id": "cnt_f6e2b9f642_doc_01",
            "type": "document",
            "schemaVersion": 1,
            "title": "海外旅行アドバンス英語 1",
            "language": "ja",
            "learningLanguage": "en",
            "documents": [
                {
                    "id": "doc-1",
                    "text": "海外旅行の最初の関門、入国審査は緊張する場面かもしれませんね。事前に準備しておきましょう。",
                }
            ],
        },
    )

    result = validate_files([file])

    errors = [error for error in result.errors if error.path == "documents" and "learning language" in error.message]
    assert errors
    assert errors[0].severity == "warning"
    assert errors[0].classification == "quality"
    assert result.valid is True


def test_document_validator_passes_when_learning_language_phrase_present() -> None:
    """学習言語フレーズが含まれる場合は error にならないことを検証。"""
    file = GeneratedFile(
        name="cnt_f6e2b9f642_doc_01.json",
        kind="document",
        content={
            "id": "cnt_f6e2b9f642_doc_01",
            "type": "document",
            "schemaVersion": 1,
            "title": "海外旅行アドバンス英語 1",
            "language": "ja",
            "learningLanguage": "en",
            "documents": [
                {
                    "id": "doc-1",
                    "text": "I'm here for tourism. これは最も一般的な答え方です。",
                }
            ],
        },
    )

    result = validate_files([file])

    errors = [error for error in result.errors if error.path == "documents.0.text" and "learning language" in error.message]
    assert not errors


def test_document_validator_skips_same_script_language_pair() -> None:
    """pack=ja, learning=ja のようにスクリプトが同じ場合は学習言語検証をスキップする。"""
    file = GeneratedFile(
        name="ja_learning_doc.json",
        kind="document",
        content={
            "id": "ja_learning_doc",
            "type": "document",
            "schemaVersion": 1,
            "title": "日本語学習",
            "language": "ja",
            "learningLanguage": "ja",
            "documents": [
                {"id": "doc-1", "text": "ひらがなの「あ」を覚えましょう。"}
            ],
        },
    )

    result = validate_files([file])

    errors = [error for error in result.errors if "learning language" in error.message]
    assert not errors


def test_validator_reports_unresolved_placeholder_without_auto_replacement() -> None:
    """[目的地] 型は品質確認対象にし、具体名への自動置換は行わない。"""
    file = GeneratedFile(
        name="travel_quiz.json",
        kind="quiz",
        content={
            "id": "travel_quiz",
            "title": "旅行英語",
            "questions": [
                {
                    "id": "q-1",
                    "question": "[目的地]までの行き方を尋ねる表現はどれですか？",
                    "choices": ["A", "B", "C", "D"],
                    "answerIndex": 0,
                    "explanation": "具体的な目的地を伝えます。",
                }
            ],
        },
    )

    result = validate_files([file])

    assert result.valid is True
    issue = next(error for error in result.errors if "unresolved learner-facing placeholder" in error.message)
    assert issue.severity == "warning"
    assert issue.classification == "quality"


def test_validator_reports_duplicate_questions_and_choices_as_quality_issues() -> None:
    quiz_file = GeneratedFile(
        name="duplicate_quiz.json",
        kind="quiz",
        content={
            "id": "duplicate_quiz",
            "title": "確認",
            "questions": [
                {"id": "q-1", "question": "同じ質問ですか？", "choices": ["A", "B", "C", "D"], "answerIndex": 0, "explanation": "E"},
                {"id": "q-2", "question": "同じ質問ですか？", "choices": ["A", "A", "C", "D"], "answerIndex": 0, "explanation": "E"},
            ],
        },
    )
    document_file = GeneratedFile(
        name="repetition_document.json",
        kind="document",
        content={
            "id": "repetition_document",
            "title": "反復練習",
            "documents": [
                {"id": "doc-1", "text": "I need help. と伝えます。"},
                {"id": "doc-2", "text": "I need help. と伝えます。"},
            ],
        },
    )

    result = validate_files([quiz_file, document_file])

    assert result.valid is True
    assert any(
        error.file == "duplicate_quiz.json"
        and error.severity == "warning"
        and error.classification == "quality"
        for error in result.errors
    )
    assert any(error.file == "repetition_document.json" and error.severity == "warning" for error in result.errors)


def test_generation_completes_with_diagnostics_when_placeholder_remains_after_regeneration(monkeypatch) -> None:
    """品質問題が残っても生成は完走し、任意の具体名へ自動置換しない。"""
    plan = _plan()
    plan.quizPacks = []
    plan.enableTtsOptimize = False
    attempts = {"count": 0}

    def invalid_document(*_args, **_kwargs):
        attempts["count"] += 1
        return SokqaDocumentPack(
            id="quality_pack_doc_01",
            title="基礎",
            documents=[{"id": "doc-1", "text": "[目的地]までの行き方を尋ねます。"}],
        )

    monkeypatch.setattr(pack_agent, "generate_document_pack", invalid_document)
    monkeypatch.setattr(pack_agent, "repair_files", lambda files: files)
    original_persist = pack_agent._persist_initial_revision
    persist_requests: list[bool] = []

    def capture_persist(plan, metadata, files, operation, persist, quality_status=None):
        persist_requests.append(persist)
        # Build the real revision without touching external storage.
        return original_persist(plan, metadata, files, operation, False)

    monkeypatch.setattr(pack_agent, "_persist_initial_revision", capture_persist)

    result = generate_pack(GeneratePackRequest(plan=plan, persist=True, ttsReadingMode="none"))

    assert result.status == "completed"
    assert attempts["count"] == 2
    assert persist_requests == [True]
    assert result.persisted is True
    assert result.generationStatus == "completed"
    assert result.qualityStatus == "warning"
    assert result.publicationStatus == "draft"
    assert result.validation.valid is True
    assert result.blockingErrors == []
    assert result.qualityIssues
    assert any("unresolved learner-facing placeholder" in error.message for error in result.validation.errors)
    assert any("continuing with reviewable output" in log for log in result.logs)


def test_quiz_prompt_pack_mode_includes_question_structure_and_good_bad_examples() -> None:
    """choiceLanguageMode=pack で、問題構造の出し分け指示と Good/Bad 例が含まれること（要件3-1）。"""
    plan = _plan()
    plan.learningLanguage = "en"
    prompt = quiz_generation_prompt(plan, _quiz_pack_with_mode("pack"), [_source_pack()])

    # 既存の選択肢言語指示が維持されていること
    assert "Write all four choices in each question in the pack language (ja)" in prompt
    # 問題構造の出し分け指示
    assert "Question structure (pack mode)" in prompt
    assert "question には学習言語（en）の表現・フレーズを提示" in prompt
    assert "四つの選択肢はすべてパック言語（ja）で書き" in prompt
    assert "explanation もパック言語（ja）で書くこと" in prompt
    # Good/Bad 例（ja/en ペア時の具体例）
    assert "Good/Bad examples (pack mode)" in prompt
    assert "It's a pleasure to finally meet you." in prompt
    assert "選択肢が学習言語になっている点が誤り" in prompt


def test_quiz_prompt_learning_mode_includes_question_structure_and_good_bad_examples() -> None:
    """choiceLanguageMode=learning で、問題構造の出し分け指示と Good/Bad 例が含まれること（要件3-2）。"""
    plan = _plan()
    plan.learningLanguage = "en"
    prompt = quiz_generation_prompt(plan, _quiz_pack_with_mode("learning"), [_source_pack()])

    # 既存の選択肢言語指示が維持されていること
    assert "Write all four choices in each question in the learning language (en)" in prompt
    # 問題構造の出し分け指示
    assert "Question structure (learning mode)" in prompt
    assert "question にはパック言語（ja）で場面・意図・ニュアンスを提示" in prompt
    assert "四つの選択肢はすべて学習言語（en）の表現で書く" in prompt
    assert "explanation はパック言語（ja）で書くこと" in prompt
    # Good/Bad 例（ja/en ペア時の具体例）
    assert "Good/Bad examples (learning mode)" in prompt
    assert "初対面の相手に丁寧に会えた喜びを伝えたいとき" in prompt
    assert "How do you do?" in prompt
    assert "choices を日本語にする" in prompt


def test_quiz_prompt_choice_language_instruction_preserved_across_modes() -> None:
    """既存の選択肢言語指示が各モードに含まれること（削除されていない確認：要件3-3）。"""
    plan = _plan()
    plan.learningLanguage = "en"

    pack_prompt = quiz_generation_prompt(plan, _quiz_pack_with_mode("pack"), [_source_pack()])
    assert "Write all four choices in each question in the pack language (ja)" in pack_prompt

    learning_prompt = quiz_generation_prompt(plan, _quiz_pack_with_mode("learning"), [_source_pack()])
    assert "Write all four choices in each question in the learning language (en)" in learning_prompt


def test_quiz_prompt_auto_mode_text_unchanged_and_no_structure_template() -> None:
    """auto モードは既存文言が維持され、構造テンプレート・Good/Bad 例が追加されていないこと（要件3-4）。"""
    plan = _plan()
    plan.learningLanguage = "en"
    prompt = quiz_generation_prompt(plan, _quiz_pack_with_mode("auto"), [_source_pack()])

    # 既存 auto 文言が維持されていること
    assert ("Choose either the pack language (ja) or learning language (en) per question. "
            "All four choices within one question must use the same chosen language. "
            "Never mix languages inside one four-choice set.") in prompt
    # 構造テンプレート・Good/Bad 例が追加されていないこと
    assert "Question structure (pack mode)" not in prompt
    assert "Question structure (learning mode)" not in prompt
    assert "Good/Bad examples (pack mode)" not in prompt
    assert "Good/Bad examples (learning mode)" not in prompt


def test_quiz_prompt_non_ja_en_pack_uses_generic_good_bad_examples_without_hardcoded_language() -> None:
    """ja/en 以外のペアでも、言語ハードコードせず言語ラベルベースの汎用例が出ること（要件: 非ハードコード）。"""
    plan = _plan()
    plan.language = "ko"
    plan.learningLanguage = "fr"
    prompt = quiz_generation_prompt(plan, _quiz_pack_with_mode("pack"), [_source_pack()])

    assert "question には学習言語（fr）の表現・フレーズを提示" in prompt
    assert "四つの選択肢はすべてパック言語（ko）で書き" in prompt
    # 固定ハードコードされた ja/en 具体例が出ないこと
    assert "It's a pleasure to finally meet you." not in prompt
    assert "How do you do?" not in prompt


def test_quiz_prompt_enforces_exact_question_count() -> None:
    """問題①: quiz_generation_prompt が questionCount と全く同じ件数の出力を強制する文言を含むこと。"""
    plan = _plan()
    plan.learningLanguage = "en"
    quiz_pack = _quiz_pack_with_mode("pack").model_copy(update={"questionCount": 30})
    prompt = quiz_generation_prompt(plan, quiz_pack, [_source_pack()])

    assert "- question count: 30" in prompt
    assert "You MUST output exactly 30 questions in the questions[] array" in prompt
    assert "Do not output fewer or more than this count" in prompt
    assert "until you reach exactly 30" in prompt


# --- Phase 9 Task 2: 角括弧プレースホルダー禁止（document + quiz 両本文） ---


def test_document_quality_rules_block_forbids_bracketed_generic_labels() -> None:
    """_document_quality_rules_block は角括弧で囲んだ汎用ラベル([国名]等)の禁止を含むこと（Phase 9 Task 2）。"""
    plan = _plan()
    block = _document_quality_rules_block(plan, json.dumps(document_global_tags(plan, plan.documents[0])))

    assert "Square-bracketed generic labels such as [国名]" in block
    for label in ["[都市名]", "[数量]", "[品物]", "[氏名]", "[飲み物]", "[番号]"]:
        assert label in block
    # 既存の square-bracket placeholder 禁止も維持
    assert "square-bracket placeholders" in block
    # タグ([en-US]等)の本文混入禁止も維持
    assert "Do not use any square-bracket tag or code such as [en-US], [ja-JP]" in block


def test_quiz_quality_rules_block_forbids_bracketed_generic_labels() -> None:
    """_quiz_quality_rules_block は角括弧で囲んだ汎用ラベル([国名]等)の禁止を含むこと（Phase 9 Task 2）。"""
    plan = _plan()
    block = _quiz_quality_rules_block(plan, json.dumps(quiz_global_tags(plan, plan.quizPacks[0])))

    assert "Square-bracketed generic labels such as [国名]" in block
    for label in ["[都市名]", "[数量]", "[品物]", "[氏名]", "[飲み物]", "[番号]"]:
        assert label in block
    assert "square-bracket placeholders" in block
    assert "Do not use any square-bracket tag or code such as [en-US], [ja-JP]" in block


def test_document_and_quiz_prompts_forbid_bracketed_generic_labels() -> None:
    """document/quiz の生成プロンプト双方に角括弧汎用ラベル禁止が含まれること（Phase 9 Task 2: doc_03 再現）。"""
    plan = _plan()
    document_prompt = document_generation_prompt(plan, plan.documents[0])
    quiz_prompt = quiz_generation_prompt(plan, plan.quizPacks[0], [_source_pack()])

    for prompt in [document_prompt, quiz_prompt]:
        assert "Square-bracketed generic labels such as [国名]" in prompt
        # doc_03 実生成で残存したラベルが禁止指示に含まれる
        assert "[都市名]" in prompt
        assert "[数量]" in prompt
        assert "[品物]" in prompt
        # quiz 実生成で残存したラベルが禁止指示に含まれる
        assert "[氏名]" in prompt
        assert "[飲み物]" in prompt
        assert "[番号]" in prompt
