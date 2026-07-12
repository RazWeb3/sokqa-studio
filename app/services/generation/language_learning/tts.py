"""Language Learning 専用 TTS 処理（Phase 7: TTS 移設）。

tts_optimizer.py から切り出した語学専用ロジックをここへ集約する。
learningLanguage / choiceLanguageMode に基づくクイズ用 TTS 言語設定を担当。
Standard 側は tts_optimizer.py に残り、Phase 7 時点は既存挙動を維持する。
"""

from app.schemas.common import TtsLanguageSettings
from app.schemas.sokqa import SokqaDocumentPack, SokqaQuizPack
from app.schemas.common import default_speech_language_code
import re


_LANGUAGE_TAG_RE = re.compile(r"\[([a-z]{2,3}(?:-[A-Za-z0-9]+)*)\]", re.I)


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


def normalize_language_tag_structure(text: str | None, pack_language: str) -> str | None:
    """Remove only provably redundant TTS switches; never infer phrase meaning.

    A switch to the current language, an empty switch interval, and an initial
    switch to the pack language have no pronunciation effect.  Ambiguous spans
    such as ``[en-US]Wi-Fiの料金`` are intentionally left untouched for the
    boundary validator to report rather than silently changing learner audio.
    """
    if not text:
        return text
    default_code = default_speech_language_code(pack_language).lower()
    current = default_code
    output: list[str] = []
    position = 0
    for match in _LANGUAGE_TAG_RE.finditer(text):
        segment = text[position:match.start()]
        if segment:
            output.append(segment)
        tag = match.group(1).lower()
        next_match = _LANGUAGE_TAG_RE.search(text, match.end())
        next_segment = text[match.end():next_match.start() if next_match else len(text)]
        if tag != current and next_segment:
            output.append(match.group(0))
            current = tag
        position = match.end()
    output.append(text[position:])
    return _strip_redundant_terminal_default_tag("".join(output), pack_language)


def normalize_language_learning_tts_tags(pack: SokqaDocumentPack | SokqaQuizPack) -> None:
    """Normalize only Language Learning multilingual TTS output in place."""
    if not pack.learningLanguage:
        return
    if isinstance(pack, SokqaDocumentPack):
        for document in pack.documents:
            if document.tts:
                document.tts.text = normalize_language_tag_structure(document.tts.text, pack.language)
        return
    for question in pack.questions:
        if not question.tts:
            continue
        question.tts.questionText = normalize_language_tag_structure(question.tts.questionText, pack.language)
        question.tts.explanationText = normalize_language_tag_structure(question.tts.explanationText, pack.language)
        if question.tts.choiceTexts:
            question.tts.choiceTexts = [
                normalize_language_tag_structure(text, pack.language) or ""
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
