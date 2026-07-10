"""content_validator の回帰テスト。

B（生成時英語フレーズ欠落ガード）: 英語スパンが中途切断されている場合を検知する。
実データ由来のパターン（Wi-Fi 抜け語脱落、料理名脱落、ハイフン中途）をカバーする。
"""

from app.services.content_validator import validate_english_spans


def _doc_content(tts_text: str, body_text: str = "") -> dict:
    return {
        "type": "document",
        "documents": [
            {"id": "doc-1", "text": body_text, "tts": {"text": tts_text}}
        ],
    }


def test_detects_wifi_dropout_mid_sentence() -> None:
    """doc_04 doc-13: 'connecting to the.' は Wi-Fi 抜けによる語脱落。"""
    content = _doc_content("説明。[en-US]I'm having trouble connecting to the.[ja-JP] と伝えてください。")
    issues = validate_english_spans(content, "doc_04.json")
    assert len(issues) == 1
    assert issues[0].unit_id == "doc-1"
    assert "connecting to the." in issues[0].span_text


def test_detects_dish_name_dropout_mid_sentence() -> None:
    """doc_05 doc-18: 'Could you tell me more about the' は料理名脱落。"""
    content = _doc_content("[en-US]Could you tell me more about the[ja-JP] と尋ねてみてください。")
    issues = validate_english_spans(content, "doc_05.json")
    assert len(issues) == 1
    assert issues[0].span_text == "Could you tell me more about the"


def test_detects_trailing_hyphen() -> None:
    """doc_01 doc-30: 'step- directions' はハイフン中途。"""
    content = _doc_content("[en-US]step- directions[ja-JP] と依頼します。")
    issues = validate_english_spans(content, "doc_01.json")
    assert len(issues) == 1
    assert issues[0].span_text == "step- directions"


def test_accepts_complete_english_span() -> None:
    """正常な完全な英語スパンは検知しない。"""
    content = _doc_content("[en-US]Excuse me, I'd like to inquire about the status of flight JL123 to London.[ja-JP] これは丁寧な表現です。")
    assert validate_english_spans(content, "doc_01.json") == []


def test_accepts_standalone_english_block_in_body() -> None:
    """本文内の独立した完全な英語ブロックは検知しない。"""
    content = _doc_content("", "Has my flight been delayed or canceled?\n「delayed」は「遅延した」という意味です。")
    assert validate_english_spans(content, "doc_01.json") == []


def test_detects_cutoff_in_standalone_body_block() -> None:
    """本文内の独立英語ブロックが中途切断されていた場合も検知する。"""
    content = _doc_content("", "Could you tell me more about the")
    issues = validate_english_spans(content, "doc_05.json")
    assert len(issues) == 1
    assert issues[0].field == "text"


def test_ignores_quiz_content() -> None:
    """クイズは対象外（別タスク）。"""
    content = {
        "type": "quiz",
        "questions": [
            {"id": "q-1", "question": "Q?", "choices": ["a"], "answerIndex": 0, "explanation": "E"}
        ],
    }
    assert validate_english_spans(content, "quiz.json") == []
