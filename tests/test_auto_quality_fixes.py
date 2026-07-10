"""apply_auto_quality_fixes の回帰テスト。

C（AUTOカテゴリ自動適用）: 生成パイプライン内で決定論的TTS問題（クイズの choiceTexts
長不一致・言語モード不整合）を自動修正する。PENDINGカテゴリ（factual/style/leak）は対象外。
"""

from app.services.quality_fixer import apply_auto_quality_fixes


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
    updated, applied = apply_auto_quality_fixes(content, "quiz.json")
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
