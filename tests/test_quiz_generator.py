import pytest
from app.config import get_settings
from app.schemas.sokqa import CoursePlan, PlanDocument, PlanQuizPack, SokqaDocumentPack
from app.services.gemini_client import GeminiClient
from app.services.quiz_generator import (
    _pack_language_violations,
    _repair_pack_language_violations,
    _supplement_quiz_questions,
)


def _plan(choice_language_mode: str = "pack", learning_language: str = "en") -> CoursePlan:
    return CoursePlan(
        id="quiz_bug_pack",
        title="海外旅行上級英会話",
        description="テスト用パック",
        targetUser="大学生",
        difficulty="advanced",
        language="ja",
        learningLanguage=learning_language,
        documents=[PlanDocument(id="doc_01", title="基礎", goal="基礎を理解する")],
        quizPacks=[
            PlanQuizPack(
                id="quiz_01",
                title="確認クイズ",
                purpose="key_concepts",
                questionCount=10,
                choiceLanguageMode=choice_language_mode,
                sourceDocumentIds=["doc_01"],
            )
        ],
    )


def _quiz_pack(plan: CoursePlan) -> PlanQuizPack:
    return plan.quizPacks[0]


def test_pack_language_violations_detects_english_choices_in_pack_mode() -> None:
    """問題②: choiceLanguageMode=pack なのに英語選択肢の問題を検知する。"""
    plan = _plan(choice_language_mode="pack", learning_language="en")
    content = {
        "questions": [
            {
                "id": "q-1",
                "question": "意味はどれですか。",
                "choices": ["〜を尋ねる", "〜を議論する", "〜を調査する", "〜を求める"],
                "answerIndex": 0,
                "explanation": "正解です。",
            },
            {
                "id": "q-6",
                "question": "観光目的はどれですか。",
                "choices": [
                    "I am visiting my friend, Kaito Tanaka.",
                    "I am here for tourism.",
                    "I am here for business.",
                    "I don't know my purpose.",
                ],
                "answerIndex": 1,
                "explanation": "正解です。",
            },
        ]
    }

    violations = _pack_language_violations(content, plan, _quiz_pack(plan))

    assert [v["id"] for v in violations] == ["q-6"]


def test_pack_language_violations_no_false_positive_when_all_japanese() -> None:
    """問題②: packモードで全選択肢が日本語の場合は違反を検知しない。"""
    plan = _plan(choice_language_mode="pack", learning_language="en")
    content = {
        "questions": [
            {
                "id": "q-1",
                "question": "意味はどれですか。",
                "choices": ["〜を尋ねる", "〜を議論する", "〜を調査する", "〜を求める"],
                "answerIndex": 0,
                "explanation": "正解です。",
            }
        ]
    }

    assert _pack_language_violations(content, plan, _quiz_pack(plan)) == []


def test_pack_language_violations_skipped_in_learning_mode() -> None:
    """問題②: choiceLanguageMode=learning の場合は検知しない。"""
    plan = _plan(choice_language_mode="learning", learning_language="en")
    content = {
        "questions": [
            {
                "id": "q-6",
                "question": "観光目的はどれですか。",
                "choices": [
                    "I am visiting my friend.",
                    "I am here for tourism.",
                    "I am here for business.",
                    "I don't know my purpose.",
                ],
                "answerIndex": 1,
                "explanation": "正解です。",
            }
        ]
    }

    assert _pack_language_violations(content, plan, _quiz_pack(plan)) == []


def test_repair_pack_language_violations_rewrites_only_violations(monkeypatch) -> None:
    """問題②: 違反問題のみを再生成し、日本語選択肢へ是正する。非違反問題は保持する。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = _plan(choice_language_mode="pack", learning_language="en")
    content = {
        "questions": [
            {
                "id": "q-1",
                "question": "意味はどれですか。",
                "choices": ["〜を尋ねる", "〜を議論する", "〜を調査する", "〜を求める"],
                "answerIndex": 0,
                "explanation": "正解です。",
            },
            {
                "id": "q-6",
                "question": "観光目的はどれですか。",
                "choices": [
                    "I am visiting my friend, Kaito Tanaka.",
                    "I am here for tourism.",
                    "I am here for business.",
                    "I don't know my purpose.",
                ],
                "answerIndex": 1,
                "explanation": "正解です。",
            },
        ]
    }

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        assert "ALL four choices are in the pack language" in prompt
        return {
            "questions": [
                {
                    "id": "q-6",
                    "choices": [
                        "友人の木村さんを訪ねます。",
                        "観光が目的です。",
                        "仕事が目的です。",
                        "目的がわかりません。",
                    ],
                    "answerIndex": 1,
                    "explanation": "正解です。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    repaired = _repair_pack_language_violations(content, plan, _quiz_pack(plan), model="gemini-2.5-flash")

    questions = repaired["questions"]
    assert len(questions) == 2
    # q-1 は保持（日本語のまま）
    assert questions[0]["choices"] == ["〜を尋ねる", "〜を議論する", "〜を調査する", "〜を求める"]
    # q-6 のみ置換（英語→日本語）
    assert questions[1]["choices"] == [
        "友人の木村さんを訪ねます。",
        "観光が目的です。",
        "仕事が目的です。",
        "目的がわかりません。",
    ]
    assert questions[1]["answerIndex"] == 1


def test_repair_pack_language_violations_returns_original_on_failure(monkeypatch) -> None:
    """問題②: 再生成失敗時は元の content をそのまま返す（部分適用しない）。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = _plan(choice_language_mode="pack", learning_language="en")
    content = {
        "questions": [
            {
                "id": "q-6",
                "question": "観光目的はどれですか。",
                "choices": [
                    "I am visiting my friend, Kaito Tanaka.",
                    "I am here for tourism.",
                    "I am here for business.",
                    "I don't know my purpose.",
                ],
                "answerIndex": 1,
                "explanation": "正解です。",
            }
        ]
    }

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        raise RuntimeError("llm failure")

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    repaired = _repair_pack_language_violations(content, plan, _quiz_pack(plan), model="gemini-2.5-flash")

    # 元の英語 choices が保持される（部分修正されていない）
    assert repaired["questions"][0]["choices"][0] == "I am visiting my friend, Kaito Tanaka."


def test_supplement_quiz_questions_appends_missing_count_without_duplicates(monkeypatch) -> None:
    """問題①案A: 不足分のみを補完生成し、既存問題を保持しつつ件数を満たす。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = _plan(choice_language_mode="pack", learning_language="en")
    content = {
        "questions": [
            {
                "id": "q-1",
                "question": "意味はどれですか。",
                "choices": ["〜を尋ねる", "〜を議論する", "〜を調査する", "〜を求める"],
                "answerIndex": 0,
                "explanation": "正解です。",
            },
            {
                "id": "q-2",
                "question": "使い方はどれですか。",
                "choices": ["丁寧に伝える", "乱暴に伝える", "無視する", "叫ぶ"],
                "answerIndex": 0,
                "explanation": "正解です。",
            },
        ]
    }

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        # 既存問題がプロンプトに列挙され、重複禁止が指示されていること
        assert "Do NOT duplicate the topic" in prompt
        assert 'q-1": 意味はどれですか' in prompt or "q-1" in prompt
        return {
            "questions": [
                {
                    "id": "q-3",
                    "question": "新しい論点ですか。",
                    "choices": ["適切な選択肢A", "不適切B", "不適切C", "不適切D"],
                    "answerIndex": 0,
                    "explanation": "正解です。",
                }
            ]
        }

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    supplemented = _supplement_quiz_questions(content, plan, _quiz_pack(plan), 1, model="gemini-2.5-flash")

    questions = supplemented["questions"]
    assert len(questions) == 3
    # 既存 q-1, q-2 が保持され、新規 q-3 が追加される（normalize で id は再採番される）
    ids = [q["id"] for q in questions]
    assert "q-3" in ids
    assert any("意味はどれですか" in q["question"] for q in questions)


def test_supplement_quiz_questions_returns_original_on_failure(monkeypatch) -> None:
    """問題①案A: 補完失敗時は元の content をそのまま返す。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_provider", "mock")

    plan = _plan(choice_language_mode="pack", learning_language="en")
    content = {
        "questions": [
            {
                "id": "q-1",
                "question": "意味はどれですか。",
                "choices": ["〜を尋ねる", "〜を議論する", "〜を調査する", "〜を求める"],
                "answerIndex": 0,
                "explanation": "正解です。",
            }
        ]
    }

    def fake_generate_json(self, prompt: str, model: str | None = None, **kwargs) -> dict:
        raise RuntimeError("llm failure")

    monkeypatch.setattr(GeminiClient, "generate_json", fake_generate_json)

    supplemented = _supplement_quiz_questions(content, plan, _quiz_pack(plan), 2, model="gemini-2.5-flash")

    # 元の件数が保持される（崩れない）
    assert len(supplemented["questions"]) == 1
    assert supplemented["questions"][0]["id"] == "q-1"
