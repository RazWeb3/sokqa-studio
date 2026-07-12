"""apply_auto_quality_fixes の回帰テスト。

C（AUTOカテゴリ自動適用）: 生成パイプライン内で決定論的TTS問題（クイズの choiceTexts
長不一致・言語モード不整合）を自動修正する。PENDINGカテゴリ（factual/style/leak）は対象外。
"""

from app.schemas.quality import QualityLocation
from app.services.quality_fixer import _is_safe_auto_tts_repair, apply_auto_quality_fixes
from app.services.generation.strategies.language_learning import LanguageLearningStrategy
from app.services.quality.context import QualityContext


def _quiz_content(questions: list[dict]) -> dict:
    return {
        "type": "quiz",
        "language": "ja",
        "learningLanguage": "en",
        "choiceLanguageMode": "learning",
        "questions": questions,
    }


def test_fixes_mismatched_choice_texts_length() -> None:
    """choiceTexts の配列長が choices と一致しない場合、修正される。"""
    content = _quiz_content(
        [
            {
                "id": "q-1",
                "question": "Q?",
                "choices": ["a", "b", "c", "d"],
                "answerIndex": 0,
                "explanation": "E",
                "tts": {"choiceTexts": ["[en-US]a", "[en-US]b"]},
            }
        ]
    )
    updated, applied = apply_auto_quality_fixes(
        content, "quiz.json", context=QualityContext(LanguageLearningStrategy())
    )
    assert len(applied) >= 1
    q = updated["questions"][0]
    assert len(q["tts"]["choiceTexts"]) == len(q["choices"])


def test_learning_mode_mismatch_is_not_auto_applied() -> None:
    """learning モードで選択肢が日本語（pack言語）の tts_text_mismatch は、suggestion が
    説明文のため自動適用対象外（PENDING扱い）。検出のみで content は変更されない。"""
    content = _quiz_content(
        [
            {
                "id": "q-1",
                "question": "Q?",
                "choices": ["観光です", "ビジネスです", "友人訪問です", "不明です"],
                "answerIndex": 0,
                "explanation": "E",
                "tts": {"choiceTexts": [None, None, None, None]},
            }
        ]
    )
    updated, applied = apply_auto_quality_fixes(content, "quiz.json")
    assert applied == []
    assert updated == content


def test_no_fix_when_already_valid() -> None:
    """正常なクイズは修正されない。"""
    content = _quiz_content(
        [
            {
                "id": "q-1",
                "question": "Q?",
                "choices": ["a", "b", "c", "d"],
                "answerIndex": 0,
                "explanation": "E",
                "tts": {
                    "choiceTexts": ["[en-US]a", "[en-US]b", "[en-US]c", "[en-US]d"],
                },
            }
        ]
    )
    updated, applied = apply_auto_quality_fixes(content, "quiz.json")
    assert applied == []
    assert updated == content


def test_document_content_returns_unchanged() -> None:
    """ドキュメントは deterministic_tts_issues の対象外（クイズ専用）のため変更なし。"""
    content = {
        "type": "document",
        "documents": [{"id": "doc-1", "text": "本文", "tts": {"text": "本文"}}],
    }
    updated, applied = apply_auto_quality_fixes(content, "doc.json")
    assert applied == []
    assert updated == content


# Regression fixtures from cnt_690bc5b8e3.  These tests intentionally split
# safe TTS-only repairs from learner-facing text proposals.


def test_tts_tag_repair_is_safe_only_when_it_reduces_to_display_text() -> None:
    """doc_06/doc-14 型: タグ追加は表示本文を変えない場合だけ許可する。"""
    content = {
        "type": "document",
        "language": "ja",
        "learningLanguage": "en",
        "documents": [
            {
                "id": "doc-14",
                "text": "I've lost my wallet. と伝えます。",
                "tts": {"text": "I've lost my wallet. と伝えます。"},
            }
        ],
    }
    location = QualityLocation(fileName="doc.json", unitId="doc-14", field="text")

    assert _is_safe_auto_tts_repair(
        content,
        location,
        "I've lost my wallet. と伝えます。",
        "[en-US]I've lost my wallet.[ja-JP] と伝えます。",
    )
    assert not _is_safe_auto_tts_repair(
        content,
        location,
        "I've lost my wallet. と伝えます。",
        "[en-US]I've lost my passport.[ja-JP] と伝えます。",
    )


def test_tts_tag_repair_rejects_duplicate_or_closing_tags() -> None:
    """q-7 型: 言語の戻しは許可するが、閉じタグ・二重タグは許可しない。"""
    content = {
        "type": "quiz",
        "questions": [
            {
                "id": "q-7",
                "question": "Q",
                "choices": ["How long will you stay? と For how long?", "b", "c", "d"],
                "answerIndex": 0,
                "explanation": "E",
                "tts": {"choiceTexts": ["How long will you stay? と For how long?", "b", "c", "d"]},
            }
        ],
    }
    location = QualityLocation(fileName="quiz.json", unitId="q-7", field="tts.choiceTexts[0]")
    before = "How long will you stay? と For how long?"

    assert _is_safe_auto_tts_repair(
        content, location, before, "[en-US]How long will you stay?[ja-JP] と [en-US]For how long?"
    )
    assert not _is_safe_auto_tts_repair(
        content, location, before, "[en-US]How long will you stay?[/en-US] と For how long?"
    )
    assert not _is_safe_auto_tts_repair(
        content, location, before, "[en-US][en-US]How long will you stay?[ja-JP] と For how long?"
    )


def test_no_fix_for_doc_25_style_candidate_that_changes_learner_text() -> None:
    """doc_05/doc-25 型: 接続を壊す削除案は生成時の自動修正対象にならない。"""
    content = {
        "type": "document",
        "documents": [
            {
                "id": "doc-25",
                "text": "予約名を聞かれることが多いので、次の表現も覚えておきましょう。予約していない場合は、正直に伝えてください。",
            }
        ],
    }
    updated, applied = apply_auto_quality_fixes(content, "doc_05.json")
    assert applied == []
    assert updated == content
