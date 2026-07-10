"""Language Learning 責務分離（Phase 0-7）の移設検証テスト。

既存テストが通常教材の回帰を担保するのに対し、このテストは
generation/language_learning/ へ切り出した語学専用処理の単体挙動を担保する。
"""

import pytest

from app.schemas.common import TtsLanguageSettings
from app.schemas.request import GeneratePackRequest, PlanPackRequest
from app.schemas.sokqa import CoursePlan, SokqaDocumentPack, SokqaQuizPack
from app.services import planner
from app.services.generation.language_learning.planner import (
    _base_language,
    infer_learning_language,
    planner_objective,
)
from app.services.generation.language_learning.prompt import (
    build_language_learning_purpose_lines,
    is_japanese_learning_plan,
    japanese_learning_difficulty_block,
    ruby_policy_block,
    structure_policy_block,
)
from app.services.generation.language_learning.quality import deterministic_tts_issues
from app.services.generation.language_learning.tts import (
    build_document_language_settings,
    effective_quiz_language_settings,
)
from app.services.generation.strategy import (
    is_language_learning_plan,
    is_language_learning_request,
    resolve_generation_strategy,
)
from app.services.generation.strategies.language_learning import LanguageLearningStrategy
from app.services.generation.strategies.standard import StandardStrategy


def _make_request(theme: str, **kw) -> GeneratePackRequest:
    plan = planner.create_course_plan(
        PlanPackRequest(theme=theme, targetUser="学習者", scale="quick", documentCount=1, **kw)
    )
    return GeneratePackRequest(plan=plan)


def _make_quiz_pack(**kw) -> SokqaQuizPack:
    return SokqaQuizPack(id="quiz-pack-1", title="テストクイズ", questions=[], **kw)


# --- 統合判定（系統A/B の集約） ---


def test_is_language_learning_request_normal_is_standard():
    request = _make_request("Git入門")
    assert isinstance(resolve_generation_strategy(request), StandardStrategy)
    assert is_language_learning_request(request) is False
    assert is_language_learning_plan(request.plan) is False


def test_is_language_learning_request_system_a_foreign_language():
    request = _make_request("海外旅行英会話", learningLanguage="en")
    assert isinstance(resolve_generation_strategy(request), LanguageLearningStrategy)
    assert is_language_learning_request(request) is True


def test_is_language_learning_request_system_b_japanese_learning():
    request = _make_request("JLPT", structurePolicy="japanese_learning", language="en")
    assert isinstance(resolve_generation_strategy(request), LanguageLearningStrategy)
    assert is_language_learning_request(request) is True


def test_is_language_learning_request_same_language_is_standard():
    request = _make_request("日本語基礎", learningLanguage="ja")
    assert isinstance(resolve_generation_strategy(request), StandardStrategy)
    assert is_language_learning_request(request) is False


# --- Planner 移設 ---


def test_planner_objective_is_scene_based_for_language_learning():
    request = PlanPackRequest(
        theme="海外旅行英会話",
        targetUser="学習者",
        scale="quick",
        documentCount=1,
        learningLanguage="en",
    )
    objective = planner_objective(request)
    assert "学習場面" in objective
    assert "利用場面" in objective
    assert "JLPT" not in objective


def test_infer_learning_language_markers():
    assert infer_learning_language("英会話レッスン") == "en"
    assert infer_learning_language("韓国語を学ぶ") == "ko"
    assert infer_learning_language("抽象代数入門") is None


def test_base_language_normalizes_locale():
    assert _base_language("en-US") == "en"
    assert _base_language("ja-JP") == "ja"
    assert _base_language(None) == ""


# --- Prompt 移設 ---


def test_japanese_learning_plan_detection():
    plan = CoursePlan(
        id="jp-plan-1",
        title="JLPT N5 日本語",
        description="日本語の基礎",
        shortTitle="N5",
        targetUser="外国人学習者",
        language="en",
        documents=[],
        quizPacks=[],
        difficulty="beginner",
    )
    assert is_japanese_learning_plan(plan) is True


def test_japanese_learning_difficulty_block_beginner():
    plan = CoursePlan(
        id="jp-plan-2",
        title="日本語入門",
        description="ひらがな",
        shortTitle="",
        targetUser="学習者",
        language="en",
        documents=[],
        quizPacks=[],
        difficulty="beginner",
    )
    block = japanese_learning_difficulty_block(plan)
    assert "N5" in block


def test_japanese_learning_structure_and_ruby_policy_present():
    plan = _dummy_plan()
    assert "japanese_learning" in structure_policy_block(plan)
    assert "furigana" in ruby_policy_block()


def _dummy_plan():
    return CoursePlan(
        id="jp-plan-3",
        title="日本語",
        description="",
        shortTitle="",
        targetUser="",
        language="en",
        documents=[],
        quizPacks=[],
        difficulty="beginner",
    )


# --- Prompt 移設（Phase 5 候補1） ---


def test_build_language_learning_purpose_lines_for_foreign_language():
    plan = CoursePlan(
        id="ll-purpose-1",
        title="海外旅行英会話",
        description="",
        shortTitle="",
        targetUser="学習者",
        language="ja",
        learningLanguage="en",
        documents=[],
        quizPacks=[],
        difficulty="beginner",
    )
    lines = build_language_learning_purpose_lines(plan)
    assert len(lines) == 2
    assert "学習対象言語" in lines[0]
    # Phase 9 Task 1: 各 documents[] セクションの「短い導入→即フレーズ→短い解説」構成強制
    assert "短い導入" in lines[1]
    assert "第1フレーズ" in lines[1]
    assert "documents[]" in lines[1]
    # Phase 9 Task 4: 本文への言語タグ混入禁止を明記（タグ無しの素のテキスト指示）
    assert "言語タグなしの素のテキスト" in lines[0]
    assert "本文(text)に [en-US]" in lines[0]
    assert "言語タグで囲むことは禁止する" in lines[1]


def test_build_language_learning_purpose_lines_excluded_for_japanese_learning():
    plan = CoursePlan(
        id="ll-purpose-2",
        title="JLPT",
        description="",
        shortTitle="",
        targetUser="学習者",
        language="en",
        learningLanguage="ja",
        structurePolicy="japanese_learning",
        documents=[],
        quizPacks=[],
        difficulty="beginner",
    )
    assert build_language_learning_purpose_lines(plan) == []


def test_build_language_learning_purpose_lines_empty_for_standard():
    plan = CoursePlan(
        id="ll-purpose-3",
        title="Git入門",
        description="",
        shortTitle="",
        targetUser="学習者",
        language="ja",
        documents=[],
        quizPacks=[],
        difficulty="beginner",
    )
    assert build_language_learning_purpose_lines(plan) == []


# --- Quality 移設 ---


def test_deterministic_tts_issues_detects_missing_tag():
    content = {
        "type": "quiz",
        "language": "ja",
        "learningLanguage": "en",
        "choiceLanguageMode": "auto",
        "questions": [
            {
                "id": "q1",
                "choices": ["Hello", "Goodbye"],
                "tts": {"choiceTexts": ["こんにちは", "さようなら"]},
            }
        ],
    }
    issues = deterministic_tts_issues("quiz.json", content)
    assert any(issue.category == "reading" for issue in issues)


def test_deterministic_tts_issues_no_learning_language_returns_empty():
    content = {"type": "quiz", "language": "ja", "questions": []}
    assert deterministic_tts_issues("quiz.json", content) == []


def test_deterministic_tts_issues_non_quiz_returns_empty():
    assert deterministic_tts_issues("doc.json", {"type": "document"}) == []


# --- TTS 移設 ---


def test_effective_quiz_language_settings_learning_mode():
    pack = _make_quiz_pack(learningLanguage="en", choiceLanguageMode="learning")
    settings = effective_quiz_language_settings(pack, None)
    assert isinstance(settings, TtsLanguageSettings)
    assert settings.choicesLanguageMode == "select"
    assert settings.choicesLanguage == "en"


def test_effective_quiz_language_settings_pack_mode():
    pack = _make_quiz_pack(learningLanguage="en", choiceLanguageMode="pack")
    settings = effective_quiz_language_settings(pack, None)
    assert settings.choicesLanguage == "pack"


def test_effective_quiz_language_settings_no_learning_returns_legacy():
    pack = _make_quiz_pack()
    legacy = TtsLanguageSettings()
    assert effective_quiz_language_settings(pack, legacy) is legacy


# --- TTS 移設（Phase 5 候補3） ---


def test_build_document_language_settings_for_learning_language():
    pack = SokqaDocumentPack(
        id="doc-pack-1",
        title="英会話教材",
        documents=[],
        learningLanguage="en",
    )
    settings = build_document_language_settings(pack)
    assert settings is not None
    assert settings.documentTextLanguageMode == "mixed"
    assert settings.documentTextLanguage == "en"


def test_build_document_language_settings_no_learning_returns_none():
    pack = SokqaDocumentPack(id="doc-pack-2", title="通常教材", documents=[])
    assert build_document_language_settings(pack) is None
