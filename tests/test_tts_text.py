from app.services.tts_text import normalize_tts_text


def test_returns_stripped_text_without_punctuation_conversion() -> None:
    assert normalize_tts_text("  ITILを確認します。  ") == "ITILを確認します。"
    assert normalize_tts_text("まず確認します. 次に実行します.") == "まず確認します. 次に実行します."
    assert normalize_tts_text("全角ピリオド．も残します") == "全角ピリオド．も残します"


def test_preserves_dot_prefixed_words_filenames_and_numeric_dots() -> None:
    assert normalize_tts_text(".git フォルダを確認します.") == ".git フォルダを確認します."
    assert normalize_tts_text(".env を編集します.") == ".env を編集します."
    assert normalize_tts_text("設定は config.json にあります.") == "設定は config.json にあります."
    assert normalize_tts_text("バージョン 1.2 を使います.") == "バージョン 1.2 を使います."
