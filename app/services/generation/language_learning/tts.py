"""Language Learning 専用 TTS 処理（Phase 7: TTS 移設）。

tts_optimizer.py から切り出した語学専用ロジックをここへ集約する。
learningLanguage / choiceLanguageMode に基づくクイズ用 TTS 言語設定を担当。
Standard 側は tts_optimizer.py に残り、Phase 7 時点は既存挙動を維持する。
"""

from app.schemas.common import TtsLanguageSettings
from app.schemas.sokqa import SokqaDocumentPack, SokqaQuizPack
from app.schemas.common import default_speech_language_code


def _strip_redundant_terminal_default_tag(text: str | None, pack_language: str) -> str | None:
    """Keep switch tags stateful for Language Learning TTS only.

    A final switch back to the pack language carries no following text, so it
    is redundant. A switch that precedes Japanese (or other pack-language)
    text is preserved.
    """
    if not text:
        return text
    default_tag = f"[{default_speech_language_code(pack_language)}]"
    value = text.rstrip()
    if value.endswith(default_tag):
        return value[: -len(default_tag)].rstrip()
    return text


def normalize_language_learning_tts_tags(pack: SokqaDocumentPack | SokqaQuizPack) -> None:
    """Normalize only Language Learning multilingual TTS output in place."""
    if not pack.learningLanguage:
        return
    if isinstance(pack, SokqaDocumentPack):
        for document in pack.documents:
            if document.tts:
                document.tts.text = _strip_redundant_terminal_default_tag(document.tts.text, pack.language)
        return
    for question in pack.questions:
        if not question.tts:
            continue
        question.tts.questionText = _strip_redundant_terminal_default_tag(question.tts.questionText, pack.language)
        question.tts.explanationText = _strip_redundant_terminal_default_tag(question.tts.explanationText, pack.language)
        if question.tts.choiceTexts:
            question.tts.choiceTexts = [
                _strip_redundant_terminal_default_tag(text, pack.language) or ""
                for text in question.tts.choiceTexts
            ]


def build_document_language_settings(pack: SokqaDocumentPack) -> TtsLanguageSettings | None:
    """語学教材用のドキュメント TTS 言語設定を返す純粋関数（tts_optimizer.optimize_document_pack の語学ブロック移設）。

    learningLanguage がある場合のみ、documentText を「mixed + 学習言語」とする設定を構築して返す。
    ない場合は None を返し、呼び出し側は渡された共通 language_settings をそのまま使う。
    副作用は持たず、既存の language_settings を書き換えない（候補1の build_language_learning_purpose_lines と同方針）。
    """
    if not pack.learningLanguage:
        return None
    return TtsLanguageSettings(
        documentTextLanguageMode="mixed",
        documentTextLanguage=pack.learningLanguage,
    )


def effective_quiz_language_settings(
    pack: SokqaQuizPack,
    legacy_settings: TtsLanguageSettings | None,
) -> TtsLanguageSettings | None:
    """語学専用のクイズ TTS 言語設定（tts_optimizer._effective_quiz_language_settings の移設）。"""
    if not pack.learningLanguage:
        return legacy_settings
    if pack.choiceLanguageMode == "learning":
        choices_mode = "select"
        choices_language = pack.learningLanguage
    elif pack.choiceLanguageMode == "pack":
        choices_mode = "select"
        choices_language = "pack"
    else:
        choices_mode = "auto"
        choices_language = pack.learningLanguage
    return TtsLanguageSettings(
        documentTextLanguageMode="mixed",
        documentTextLanguage=pack.learningLanguage,
        questionLanguageMode="mixed",
        questionLanguage=pack.learningLanguage,
        choicesLanguageMode=choices_mode,
        choicesLanguage=choices_language,
        explanationLanguageMode="mixed",
        explanationLanguage=pack.learningLanguage,
    )
