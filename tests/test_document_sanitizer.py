"""Document sanitizer の回帰テスト。

Commit 1: LANGUAGE_CODE_RE が「ハイフン付き英単語」を言語コードと誤認して
本文から削除するバグ（実データで Wi-Fi が消失した事象）の防止用。
"""

from app.services.document_generator import sanitize_learner_facing_text


def test_sanitize_does_not_remove_wifi() -> None:
    text = "Wi-Fiは今や旅行に欠かせません。"
    assert sanitize_learner_facing_text(text) == text


def test_sanitize_keeps_hyphen_words() -> None:
    text = "X-ray や T-shirt は一般単語で、step-by-step の手順も残る。"
    assert sanitize_learner_facing_text(text) == text


def test_sanitize_keeps_email_phrase() -> None:
    text = "Please send me an e-mail for confirmation."
    assert sanitize_learner_facing_text(text) == text


def test_sanitize_removes_language_code() -> None:
    text = "This is en-US example and ja-JP tag should go."
    result = sanitize_learner_facing_text(text)
    assert "en-US" not in result
    assert "ja-JP" not in result


def test_sanitize_removes_all_supported_language_codes() -> None:
    codes = ["ja-JP", "en-US", "zh-CN", "ko-KR", "es-ES", "fr-FR", "de-DE", "it-IT", "pt-PT", "id-ID"]
    for code in codes:
        text = f"Before {code} after."
        assert code not in sanitize_learner_facing_text(text), f"{code} was not sanitized"
