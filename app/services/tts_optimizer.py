import logging
import re

from app.config import get_settings
from app.schemas.common import TtsLanguageSettings, TtsReadingMode, TtsRule, default_speech_language_code, normalize_tts_reading_mode
from app.schemas.sokqa import (
    DocumentTts,
    GeneratedFile,
    QuizTts,
    SokqaDocumentPack,
    SokqaQuizPack,
    TtsReport,
    TtsReportItem,
)
from app.services.gemini_client import GeminiClient
from app.services.language_detection import language_script, leading_script
from app.services.tts_text import collapse_duplicate_katakana_parentheticals, normalize_tts_text, strip_choice_separator
from app.services.tts_rules import load_system_tts_rules, load_user_tts_rules, merge_tts_rules


MAX_TTS_BATCH_CHARS = 12000
logger = logging.getLogger("sokqa_course_pack_agent")

_KATAKANA_TOKEN_RE = re.compile(r"[ァ-ヶー・]{2,}")
_KATAKANA_WORD_RE = re.compile(r"^[ァ-ヶー・]{2,}$")
_DUP_SEPARATORS = set(" \t\r\n　、。，．,.")
_BLANK_PLACEHOLDER_RE = re.compile(r"\s*[_＿]{3,}\s*")
_JAPANESE_CIRCLE_PLACEHOLDER_RE = re.compile(r"[◯○]{2,}")
_TTS_SILENT_BLANK = "  "
_SOURCE_KANJI_RE = re.compile(r"[一-龥々〆ヵヶ]")
_SOURCE_KANJI_LOSS_MIN_COUNT = 4
_SOURCE_KANJI_RETAIN_RATIO_THRESHOLD = 0.4
_EDIT_TRACE_PATTERNS = [
    {
        "issue_type": "particle_sequence_edit",
        "pattern": re.compile(r"(?:をを|がを|にを|のを|だとを|とはを)"),
        "recommendation": "助詞の連続が不自然で、LLMが読み補助ではなく文章編集に入った疑いがあります。このフィールドは辞書ベースの読みへフォールバックしました。",
    }
]


def _katakana_word(value: str) -> str | None:
    token = str(value or "").strip()
    return token if _KATAKANA_WORD_RE.fullmatch(token) else None


def _next_katakana_word(value: str) -> str | None:
    text = str(value or "")
    index = 0
    while index < len(text) and (text[index].isspace() or text[index] in _DUP_SEPARATORS):
        index += 1
    match = _KATAKANA_TOKEN_RE.match(text, index)
    return match.group(0) if match and match.start() == index else None


def _prev_katakana_word(value: str) -> str | None:
    text = str(value or "")
    end = len(text)
    while end > 0 and (text[end - 1].isspace() or text[end - 1] in _DUP_SEPARATORS):
        end -= 1
    if end <= 0:
        return None
    trimmed = text[:end]
    matches = list(_KATAKANA_TOKEN_RE.finditer(trimmed))
    if not matches:
        return None
    last = matches[-1]
    return last.group(0) if last.end() == len(trimmed) else None


def _consume_leading_separators(value: str) -> int:
    text = str(value or "")
    index = 0
    while index < len(text) and (text[index].isspace() or text[index] in _DUP_SEPARATORS):
        index += 1
    return index


def _ordered_source_kanji(text: str) -> list[str]:
    return list(dict.fromkeys(_SOURCE_KANJI_RE.findall(str(text or ""))))


def _match_snippet(text: str, match: re.Match[str], radius: int = 12) -> str:
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return text[start:end]


def _source_kanji_loss_issue(
    file_name: str,
    item_id: str,
    field: str,
    source_text: str,
    tts_text: str,
) -> TtsReportItem | None:
    if leading_script(tts_text) == "latin":
        return None
    source_kanji = _ordered_source_kanji(source_text)
    if len(source_kanji) < _SOURCE_KANJI_LOSS_MIN_COUNT:
        return None
    retained_kanji = [char for char in source_kanji if char in tts_text]
    retain_ratio = len(retained_kanji) / len(source_kanji)
    if retain_ratio > _SOURCE_KANJI_RETAIN_RATIO_THRESHOLD:
        return None
    missing_kanji = "".join(char for char in source_kanji if char not in tts_text)[:16] or None
    return TtsReportItem(
        file=file_name,
        itemId=item_id,
        field=field,
        issueType="source_kanji_loss",
        snippet=tts_text[:32],
        recommendation="原文にあった漢字の大半が消失しており、全文かな化または大規模書き換えの疑いがあります。このフィールドは辞書ベースの読みへフォールバックしました。",
        suggestedRuleSource=missing_kanji,
    )


def _editing_trace_issues(
    file_name: str,
    item_id: str,
    field: str,
    tts_text: str,
) -> list[TtsReportItem]:
    issues: list[TtsReportItem] = []
    for definition in _EDIT_TRACE_PATTERNS:
        match = definition["pattern"].search(tts_text)
        if not match:
            continue
        issues.append(
            TtsReportItem(
                file=file_name,
                itemId=item_id,
                field=field,
                issueType=definition["issue_type"],
                snippet=_match_snippet(tts_text, match),
                recommendation=definition["recommendation"],
                suggestedRuleSource=None,
            )
        )
    return issues


def _tts_editing_issues(
    file_name: str,
    item_id: str,
    field: str,
    source_text: str,
    tts_text: str,
) -> list[TtsReportItem]:
    issues: list[TtsReportItem] = []
    kanji_loss_issue = _source_kanji_loss_issue(file_name, item_id, field, source_text, tts_text)
    if kanji_loss_issue:
        issues.append(kanji_loss_issue)
    issues.extend(_editing_trace_issues(file_name, item_id, field, tts_text))
    return issues


def _is_source_inside_existing_reading_parentheses(text: str, start: int, rule: TtsRule) -> bool:
    prefix = text[:start]
    paren_index = max(prefix.rfind("（"), prefix.rfind("("))
    if paren_index < 0:
        return False
    close_index = max(prefix.rfind("）"), prefix.rfind(")"))
    if close_index > paren_index:
        return False
    before_paren = prefix[:paren_index].rstrip()
    return before_paren.endswith(rule.reading)


def _replace_rule_source(value: str, rule: TtsRule) -> str:
    if not rule.source:
        return value
    chunks: list[str] = []
    cursor = 0
    source_len = len(rule.source)
    reading_token = _katakana_word(rule.reading)
    while True:
        index = value.find(rule.source, cursor)
        if index < 0:
            chunks.append(value[cursor:])
            break
        chunks.append(value[cursor:index])
        if _is_source_inside_existing_reading_parentheses(value, index, rule):
            chunks.append(rule.source)
        else:
            prefix = "".join(chunks)
            next_text = value[index + source_len :]
            previous_token = _prev_katakana_word(prefix) if reading_token else None
            next_token = _next_katakana_word(next_text) if reading_token else None
            if reading_token and (previous_token == reading_token or next_token == reading_token):
                cursor = index + source_len + _consume_leading_separators(next_text)
                continue
            chunks.append(rule.reading)
        cursor = index + source_len
    return "".join(chunks)


def _apply_rule_replacements(value: str, rules: list[TtsRule]) -> str:
    result = value
    for rule in sorted(rules, key=lambda item: len(item.source), reverse=True):
        result = _replace_rule_source(result, rule)
    return result


def _apply_blank_placeholder_silence(value: str) -> str:
    return _BLANK_PLACEHOLDER_RE.sub(_TTS_SILENT_BLANK, value)


def _apply_japanese_placeholder_readings(value: str) -> str:
    return _JAPANESE_CIRCLE_PLACEHOLDER_RE.sub("まるまる", value)


def _apply_legacy_placeholder_fallbacks(value: str, language: str | None = None) -> str:
    result = str(value or "")
    if _base_language(language) == "ja":
        result = _apply_japanese_placeholder_readings(result)
    return _apply_blank_placeholder_silence(result)


def _speech_text(value: str, rules: list[TtsRule], language: str | None = "ja") -> str:
    result = _apply_rule_replacements(value, rules)
    result = _apply_legacy_placeholder_fallbacks(result, language)
    result = collapse_duplicate_katakana_parentheticals(result)
    return normalize_tts_text(result)


def _base_language(language: str | None) -> str:
    return (language or "ja").split("-")[0].lower()


def _language_script(language: str | None) -> str:
    base = _base_language(language)
    if base in {"ja"}:
        return "japanese"
    if base in {"zh"}:
        return "cjk"
    if base in {"ko"}:
        return "hangul"
    if base in {"en", "es", "fr", "de", "it", "pt", "id"}:
        return "latin"
    if base in {"ru", "uk", "bg", "sr"}:
        return "cyrillic"
    if base in {"ar", "fa", "ur"}:
        return "arabic"
    if base == "th":
        return "thai"
    return "latin"


def _resolve_language_selection(selected_language: str | None, pack_language: str) -> str | None:
    return pack_language if selected_language == "pack" else selected_language


def _script_languages(pack_language: str, language_settings: TtsLanguageSettings | None, field_key: str, multilingual: bool) -> set[str]:
    languages = {pack_language}
    if multilingual and language_settings:
        mode = getattr(language_settings, f"{field_key}LanguageMode", "auto")
        selected_language = _resolve_language_selection(getattr(language_settings, f"{field_key}Language", None), pack_language)
        if mode in {"mixed", "select"} and selected_language:
            languages.add(selected_language)
    return languages


def _allowed_scripts(pack_language: str, language_settings: TtsLanguageSettings | None, field_key: str, multilingual: bool) -> set[str]:
    return {_language_script(language) for language in _script_languages(pack_language, language_settings, field_key, multilingual)}


def _is_allowed_tts_char(char: str, allowed_scripts: set[str] | None = None) -> bool:
    allowed_scripts = allowed_scripts or {"japanese"}
    code = ord(char)
    if char in "\t\n\r" or 0x0020 <= code <= 0x007E:
        return True
    if 0x3000 <= code <= 0x303F or 0xFF00 <= code <= 0xFFEF:
        return True
    if "japanese" in allowed_scripts and (
        0x3040 <= code <= 0x309F
        or 0x30A0 <= code <= 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
    ):
        return True
    if "cjk" in allowed_scripts and (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
    ):
        return True
    if "hangul" in allowed_scripts and (
        0x1100 <= code <= 0x11FF
        or 0x3130 <= code <= 0x318F
        or 0xAC00 <= code <= 0xD7AF
    ):
        return True
    if "latin" in allowed_scripts and (0x00C0 <= code <= 0x024F):
        return True
    if "cyrillic" in allowed_scripts and (0x0400 <= code <= 0x052F):
        return True
    if "arabic" in allowed_scripts and (0x0600 <= code <= 0x06FF):
        return True
    if "thai" in allowed_scripts and (0x0E00 <= code <= 0x0E7F):
        return True
    return False


def _remove_spaces(value: str) -> str:
    """半角スペース（\\x20）のみを除去した文字列を返す。全角スペース・タブ・改行は除去しない。"""
    return value.replace("\x20", "")


def _remove_surplus_spaces(value: str, fallback: str) -> str:
    """value から fallback より余分な半角スペースを除去した文字列を返す。

    value と fallback は半角スペース以外が完全一致している前提。
    fallback 内のスペース位置を anchor に、value 内の対応する位置に余分な
    半角スペースがあれば除去する。
    """
    result: list[str] = []
    vi = 0  # index into value
    fi = 0  # index into fallback
    while vi < len(value) and fi < len(fallback):
        if fallback[fi] == " ":
            # anchor: fallback has a space here → consume value chars up to and including one space
            while vi < len(value) and value[vi] != " ":
                result.append(value[vi])
                vi += 1
            if vi < len(value):
                result.append(value[vi])  # keep exactly one space
                vi += 1
            fi += 1
        else:
            # fallback has non-space → skip any surplus spaces in value, then consume matching non-space
            while vi < len(value) and value[vi] == " ":
                vi += 1  # drop surplus spaces
            if vi < len(value):
                result.append(value[vi])
                vi += 1
            fi += 1
    # any remaining chars in value (should only be trailing spaces, if any)
    while vi < len(value):
        if value[vi] != " ":
            result.append(value[vi])
        vi += 1
    return "".join(result)


def _unexpected_script_snippet(value: str, allowed_scripts: set[str] | None = None) -> str | None:
    for index, char in enumerate(value):
        if not _is_allowed_tts_char(char, allowed_scripts):
            return value[max(0, index - 16) : index + 17]
    return None


def _guard_llm_text(
    value: str | None,
    fallback: str | None,
    *,
    source_text: str,
    file_name: str,
    item_id: str,
    field: str,
    warnings: list[TtsReportItem],
    allow_language_tags: bool = False,
    allowed_scripts: set[str] | None = None,
) -> str | None:
    if not value:
        return value
    value = _normalize_language_tag_markup(value)
    if not allow_language_tags:
        value = re.sub(r"\[[a-z]{2,3}(?:-[A-Z]{2})?\]", "", value)
    if fallback and value != fallback and _remove_spaces(value) == _remove_spaces(fallback) and value.count(" ") > fallback.count(" "):
        value = _remove_surplus_spaces(value, fallback)
    snippet = _unexpected_script_snippet(value, allowed_scripts)
    if snippet:
        logger.warning(
            "tts_optimizer.unexpected_script_fallback file=%s item=%s field=%s snippet=%s",
            file_name,
            item_id,
            field,
            snippet,
        )
        warnings.append(
            TtsReportItem(
                file=file_name,
                itemId=item_id,
                field=field,
                issueType="unexpected_script",
                snippet=snippet,
                recommendation="LLMのTTS補正に想定外の文字体系が混入したため、このフィールドは辞書ベースの読みへフォールバックしました。",
                suggestedRuleSource=None,
            )
        )
        return fallback
    editing_issues = _tts_editing_issues(file_name, item_id, field, source_text, value)
    if editing_issues:
        logger.warning(
            "tts_optimizer.editing_fallback file=%s item=%s field=%s issue_types=%s",
            file_name,
            item_id,
            field,
            ",".join(issue.issueType for issue in editing_issues),
        )
        warnings.extend(editing_issues)
        return fallback
    return value


def _guard_llm_quiz_tts(
    question,
    tts: QuizTts | None,
    rules: list[TtsRule],
    *,
    file_name: str,
    warnings: list[TtsReportItem],
    allow_language_tags: bool = False,
    pack_language: str = "ja",
    language_settings: TtsLanguageSettings | None = None,
) -> QuizTts | None:
    if not tts:
        return tts
    fallback = _rule_quiz_question_tts(question, rules, language=pack_language, allow_language_tags=allow_language_tags, language_settings=language_settings)
    fallback_choice_texts = fallback.choiceTexts if fallback and fallback.choiceTexts else None
    question_text = _guard_llm_text(
        tts.questionText,
        fallback.questionText if fallback else None,
        source_text=question.question,
        file_name=file_name,
        item_id=question.id,
        field="questionText",
        warnings=warnings,
        allow_language_tags=allow_language_tags,
        allowed_scripts=_allowed_scripts(pack_language, language_settings, "question", allow_language_tags),
    )
    explanation_text = _guard_llm_text(
        tts.explanationText,
        fallback.explanationText if fallback else None,
        source_text=question.explanation,
        file_name=file_name,
        item_id=question.id,
        field="explanationText",
        warnings=warnings,
        allow_language_tags=allow_language_tags,
        allowed_scripts=_allowed_scripts(pack_language, language_settings, "explanation", allow_language_tags),
    )
    choice_texts = list(tts.choiceTexts) if tts.choiceTexts else None
    if choice_texts:
        for index, value in enumerate(choice_texts):
            fallback_value = fallback_choice_texts[index] if fallback_choice_texts and index < len(fallback_choice_texts) else ""
            choice_texts[index] = _guard_llm_text(
                value,
                fallback_value,
                source_text=question.choices[index] if index < len(question.choices) else "",
                file_name=file_name,
                item_id=question.id,
                field=f"choiceTexts.{index}",
                warnings=warnings,
                allow_language_tags=allow_language_tags,
                allowed_scripts=_allowed_scripts(pack_language, language_settings, "choices", allow_language_tags),
            ) or ""
            if allow_language_tags and not choice_texts[index]:
                choice_texts[index] = question.choices[index] if index < len(question.choices) else ""
        if not any(choice_texts):
            choice_texts = None
    if not question_text and not explanation_text and not choice_texts:
        return None
    return QuizTts(
        questionText=question_text,
        choiceTexts=choice_texts,
        answerText=None,
        explanationText=explanation_text,
    )


def _combined_rules(rules: list[TtsRule]) -> list[TtsRule]:
    return merge_tts_rules(load_system_tts_rules(), load_user_tts_rules(), rules)


def _rules_for_mode(rules: list[TtsRule], mode: TtsReadingMode) -> list[TtsRule]:
    if mode in {"rule", "llm"}:
        return _combined_rules(rules)
    return []


def _effective_quiz_language_settings(
    pack: SokqaQuizPack,
    legacy_settings: TtsLanguageSettings | None,
) -> TtsLanguageSettings | None:
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


def _has_rule_match(text: str, rules: list[TtsRule]) -> bool:
    if any(rule.source in text for rule in rules):
        return True
    return False


def _needs_tts_locally(text: str, rules: list[TtsRule]) -> bool:
    if _has_rule_match(text, rules):
        return True
    if _BLANK_PLACEHOLDER_RE.search(text) or _JAPANESE_CIRCLE_PLACEHOLDER_RE.search(text):
        return True
    risky_markers = ["API", "AI", "UI", "UX", "SQL", "JSON", "CPU", "PC", "URL"]
    if any(marker in text for marker in risky_markers):
        return True
    if re.search(r"`[^`]+`", text):
        return True
    if re.search(r"\.[A-Za-z][A-Za-z0-9_-]*", text):
        return True
    if re.search(r"[A-Z]{2,}", text):
        return True
    return False


def _tts_reading_prompt(text: str, rules: list[TtsRule]) -> str:
    return f"""
Return strict JSON only. Do not use markdown fences.

Create a Sokqa TTS reading text for the fixed source text.

{_tts_reading_rules_block(rules)}

Contrast examples:
- .gitignore -> ドット ギットイグノア
- 1.2 -> いってんに

Source text:
{text}

Return this shape:
{{
  "text": "tts reading text"
}}
""".strip()


def _rules_text(rules: list[TtsRule]) -> str:
    return "\n".join(f"- {rule.source} -> {rule.reading}" for rule in rules) or "- none"


def _tts_reading_rules_block(rules: list[TtsRule]) -> str:
    return f"""
Rules:
- あなたの役割は、表示用文章を編集することではなく、読み上げ用テキストを作成することです。
- 守る対象: 文の意味、構造、語順、助詞、句読点、文体。これらは編集しないでください。
- 変更してよい対象: 読み補助が必要な語句（英略語・記号・固有名詞・難読語・文脈で読みが変わる語・辞書指定語）だけを、その語句の読みへ置き換えてください。
- 読み補助が不要な語句は変更しないでください。
- 文章全体を読み仮名へ変換してはいけません。読み補助が必要な語句だけを書き換えてください。
- Preserve the meaning and sentence order.
- Convert only pronunciation-sensitive terms to readable Japanese/kana where useful.
- This kana conversion is only for pronunciation-sensitive terms, acronyms, symbols, and proper nouns. Do not transliterate a full English sentence or phrase into katakana; keep English phrases in the original English text.
- A dot is read as "ドット" only when it is immediately followed by an ASCII letter, matching \\.[a-zA-Z].
- Keep the original Japanese punctuation as-is. Do not convert sentence-ending "。" to "、", and do not add or remove punctuation.
- A period "." between digits or inside numbers/codes must stay as the source; do not convert it.
- Do not read dots between digits as "ドット"; for example, 1.2 should be read like "いってんに".
- If an unfamiliar dot-prefixed word or acronym appears, infer a natural katakana reading from the examples.
- If a katakana reading and the immediately following parenthetical would become the same spoken word, keep it only once. For example, Governance（ガバナンス） should become ガバナンス, not ガバナンス（ガバナンス）.

Contrast examples:
- .gitignore -> ドット ギットイグノア
- 1.2 -> いってんに

Pronunciation examples. Treat these as normative examples, not as the only allowed replacements:
{_rules_text(rules)}
""".strip()


def _language_tag_rules(language: str, allow_language_tags: bool) -> str:
    speech_code = default_speech_language_code(language)
    if not allow_language_tags:
        return """
Language tag rules:
- Do not output language tags such as [en-US], [ja-JP], or any [xx-YY] marker.
- Keep the entire TTS reading text in the default language without bracketed language switches.
""".strip()
    return f"""
Language tag rules:
- Use Sokqa bracket tags such as [ja-JP] or [en-US]. Do not output XML tags such as <lang xml:lang="ja-JP"> and do not output closing tags such as </lang>.
- The scenario default language is "{language}" and its speech code is "{speech_code}". Default-language text should not start with a language tag.
- Add a tag only when a span switches to a non-default language.
- Add the default-language tag only when returning from a non-default language to the default language within the same text item.
- A text item may end while still in a non-default language; the next item starts in the default language automatically, so do not append a default-language tag at the end just to close the item.
""".strip()


LANGUAGE_TAG_RE = re.compile(r"\[[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\]")
XML_LANGUAGE_TAG_RE = re.compile(r"<lang\s+xml:lang=[\"']([A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)[\"']\s*>", re.IGNORECASE)
XML_LANGUAGE_CLOSE_RE = re.compile(r"</lang\s*>|\[/lang\]", re.IGNORECASE)
BRACKET_LANGUAGE_CLOSE_RE = re.compile(r"\[/[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*\]")


def _normalize_language_tag_markup(value: str) -> str:
    value = XML_LANGUAGE_TAG_RE.sub(lambda match: f"[{default_speech_language_code(match.group(1))}]", value)
    value = XML_LANGUAGE_CLOSE_RE.sub("", value)
    value = BRACKET_LANGUAGE_CLOSE_RE.sub("", value)
    matches = list(LANGUAGE_TAG_RE.finditer(value))
    if not matches:
        return value
    first_match = matches[0]
    if first_match.start() == 0:
        after = first_match.end()
        while after < len(value) and value[after] == " ":
            after += 1
        value = value[: first_match.end()] + value[after:]
        matches = list(LANGUAGE_TAG_RE.finditer(value))
    if matches:
        last_match = matches[-1]
        if last_match.end() == len(value):
            before = last_match.start()
            while before > 0 and value[before - 1] == " ":
                before -= 1
            value = value[:before] + value[last_match.start() :]
    return value


def _language_tag(language: str | None) -> str:
    return f"[{default_speech_language_code(language)}]"


def _strip_language_tags(value: str) -> str:
    value = _normalize_language_tag_markup(value)
    return LANGUAGE_TAG_RE.sub("", value)


def _strip_edge_default_tags(value: str, default_language: str) -> str:
    value = _normalize_language_tag_markup(value)
    tag = re.escape(_language_tag(default_language))
    value = re.sub(rf"^(?:{tag})+", "", value)
    value = re.sub(rf"(?:{tag})+$", "", value)
    return value


def _field_language_mode(settings: TtsLanguageSettings | None, field_key: str, default_language: str | None = None) -> tuple[str, str | None]:
    if not settings:
        return "auto", None
    selected_language = getattr(settings, f"{field_key}Language", None)
    if default_language is not None:
        selected_language = _resolve_language_selection(selected_language, default_language)
    return getattr(settings, f"{field_key}LanguageMode", "auto"), selected_language


def _choice_language_mode(settings: TtsLanguageSettings | None, default_language: str | None = None) -> tuple[str, str | None]:
    return _field_language_mode(settings, "choices", default_language)


def _apply_field_language_tags(
    source_text: str,
    reading_text: str,
    *,
    field_key: str,
    default_language: str,
    allow_language_tags: bool,
    language_settings: TtsLanguageSettings | None,
) -> str:
    text = _normalize_language_tag_markup(str(reading_text or source_text))
    if not allow_language_tags:
        return _strip_language_tags(text)
    mode, selected_language = _field_language_mode(language_settings, field_key, default_language)
    default_base = _base_language(default_language)
    selected_base = _base_language(selected_language) if selected_language else None
    selected_tag = _language_tag(selected_language) if selected_language else ""
    if mode == "select" and selected_language:
        if selected_base == default_base:
            return _strip_language_tags(text)
        text = _strip_edge_default_tags(text, default_language)
        text = re.sub(rf"^(?:{re.escape(selected_tag)})+", "", text)
        return f"{selected_tag}{text or source_text}"
    return _strip_edge_default_tags(text, default_language) or source_text


def _apply_choice_language_tags(
    choices: list[str],
    choice_readings: list[str],
    *,
    default_language: str,
    allow_language_tags: bool,
    language_settings: TtsLanguageSettings | None,
) -> list[str]:
    if not allow_language_tags:
        return choice_readings
    mode, selected_language = _choice_language_mode(language_settings, default_language)
    default_base = _base_language(default_language)
    selected_base = _base_language(selected_language) if selected_language else None
    selected_tag = _language_tag(selected_language) if selected_language else ""
    normalized: list[str] = []
    for index, source in enumerate(choices):
        reading = choice_readings[index] if index < len(choice_readings) else source
        text = strip_choice_separator(_normalize_language_tag_markup(str(reading or source)))
        if mode == "select" and selected_language:
            if selected_base == default_base:
                text = _strip_language_tags(text)
            else:
                text = _strip_edge_default_tags(text, default_language)
                text = re.sub(rf"^(?:{re.escape(selected_tag)})+", "", text)
                text = f"{selected_tag}{text or source}"
        elif mode == "mixed":
            text = _strip_edge_default_tags(text, default_language)
            if not text:
                text = source
        normalized.append(text or source)
    return normalized


def _field_language_instruction(label: str, field_key: str, settings: TtsLanguageSettings | None, default_language: str | None = None) -> str:
    if not settings:
        return f"- {label}: auto-detect only when the source text clearly switches language."
    mode = getattr(settings, f"{field_key}LanguageMode", "auto")
    selected_language = _resolve_language_selection(getattr(settings, f"{field_key}Language", None), default_language or "")
    if mode == "select" and selected_language:
        return (
            f"- {label}: read this field in {selected_language} ({default_speech_language_code(selected_language)}). "
            "If this differs from the default language, put that language tag at the start of the field only; do not append a default-language tag at the end."
        )
    if mode == "mixed":
        if selected_language and _base_language(selected_language) != _base_language(default_language):
            return (
                f"- {label}: mixed-language field. Write explanatory text in the default pack language. "
                f"When the learning target language or another non-default span appears, output that span in {selected_language} ({default_speech_language_code(selected_language)}) "
                f"with an inline tag, especially {selected_language} ({default_speech_language_code(selected_language)}), then add the default-language tag only where the same field returns to the default language."
            )
        return (
            f"- {label}: mixed-language field. Write explanatory text in the default pack language. "
            "Use inline tags only for clear learning-target or other non-default spans, and add the default-language tag only when returning to the default language."
        )
    return f"- {label}: auto-detect. Add inline tags only when the text clearly contains a non-default language."


def _language_policy_block(settings: TtsLanguageSettings | None, fields: list[tuple[str, str]], default_language: str | None = None) -> str:
    lines = [_field_language_instruction(label, field_key, settings, default_language) for label, field_key in fields]
    return "Field language policy:\n" + "\n".join(lines)


def _llm_response_items(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        items = data.get("items", [])
        return items if isinstance(items, list) else []
    return []


def _llm_response_ids(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        ids = data.get("ids", [])
        return ids if isinstance(ids, list) else []
    return []


def _llm_response_object_or_raise(data, *, context: str) -> dict:
    if isinstance(data, dict):
        return data
    raise ValueError(f"{context} response must be an object")


def _gemini_speech_text(value: str, rules: list[TtsRule], language: str | None = "ja") -> str:
    data = GeminiClient().generate_json(_tts_reading_prompt(value, rules))
    data = _llm_response_object_or_raise(data, context="tts reading")
    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return _speech_text(value, rules, language)
    return _speech_text(text, rules, language)


def _chunk_entries(entries: list[tuple[str, str]], max_chars: int = MAX_TTS_BATCH_CHARS) -> list[list[tuple[str, str]]]:
    chunks: list[list[tuple[str, str]]] = []
    current: list[tuple[str, str]] = []
    current_chars = 0
    for entry_id, text in entries:
        entry_chars = len(entry_id) + len(text)
        if current and current_chars + entry_chars > max_chars:
            chunks.append(current)
            current = []
            current_chars = 0
        current.append((entry_id, text))
        current_chars += entry_chars
    if current:
        chunks.append(current)
    return chunks


def _tts_batch_document_prompt(
    entries: list[tuple[str, str]],
    rules: list[TtsRule],
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
) -> str:
    entries_text = "\n".join(f"- id: {entry_id}\n  text: {text}" for entry_id, text in entries)
    return f"""
Return strict JSON only. Do not use markdown fences.

Create Sokqa TTS reading texts for the fixed source texts.

{_tts_reading_rules_block(rules)}

{_language_tag_rules(language, allow_language_tags)}

{_language_policy_block(language_settings, [("document text", "documentText")], language)}

Source texts:
{entries_text}

Return the same ids exactly. Do not add, remove, reorder, or rename ids.
Return this shape:
{{
  "items": [
    {{"id": "source-id", "text": "tts reading text"}}
  ]
}}
""".strip()


def _gemini_document_speech_map(
    entries: list[tuple[str, str]],
    rules: list[TtsRule],
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
) -> dict[str, str]:
    readings: dict[str, str] = {}
    source_by_id = {entry_id: text for entry_id, text in entries}
    for chunk in _chunk_entries(entries):
        data = GeminiClient().generate_json(_tts_batch_document_prompt(chunk, rules, language, allow_language_tags, language_settings))
        items = _llm_response_items(data)
        for item in items:
            if not isinstance(item, dict):
                continue
            entry_id = str(item.get("id", ""))
            text = item.get("text", "")
            if entry_id in source_by_id and isinstance(text, str) and text.strip():
                readings[entry_id] = _speech_text(text, rules, language)
        for entry_id, source_text in chunk:
            readings.setdefault(entry_id, _speech_text(source_text, rules, language))
    return readings


def _tts_quiz_question_prompt(
    question_id: str,
    question: str,
    choices: list[str],
    explanation: str,
    rules: list[TtsRule],
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
) -> str:
    choices_text = "\n".join(f"- index: {index}\n  text: {choice}" for index, choice in enumerate(choices))
    return f"""
Return strict JSON only. Do not use markdown fences.

Create Sokqa TTS reading texts for one fixed quiz question.

{_tts_reading_rules_block(rules)}

Quiz punctuation rules:
- Keep questionText and explanationText punctuation as natural speech cues. Do not remove sentence-final "?", "？", "!", "！", "." or Japanese punctuation.
- Choice readings are independent tracks. Do not add trailing separator commas to choice texts, but keep meaningful final ".", "?", "？", "!", and "！".
- For English learning phrases, keep the original English text. In multilingual mode, output English spans as [en-US]original English[ja-JP] when returning to Japanese. Bad: [en-US]ウィッチ グリーティング... Good: [en-US]Which greeting is...[ja-JP]

{_language_tag_rules(language, allow_language_tags)}

{_language_policy_block(language_settings, [("questionText", "question"), ("choiceTexts", "choices"), ("explanationText", "explanation")], language)}

Question id: {question_id}
Question text:
{question}

Choices:
{choices_text}

Explanation text:
{explanation}

Return the same question id exactly. Return each choice by its original index.
Return this shape:
{{
  "id": "{question_id}",
  "questionText": "tts reading text",
  "choices": [
    {{"index": 0, "text": "tts reading text"}}
  ],
  "explanationText": "tts reading text"
}}
""".strip()


def _quiz_question_source_text(question) -> str:
    return " ".join([question.question, *question.choices, question.explanation])


def _quiz_question_char_count(question) -> int:
    return len(question.id) + len(question.question) + len(question.explanation) + sum(len(choice) for choice in question.choices)


def _sparse_choice_texts(question, choice_readings: list[str], *, keep_all: bool = False) -> list[str] | None:
    choice_texts: list[str] = []
    for index, choice in enumerate(question.choices):
        reading = choice_readings[index] if index < len(choice_readings) else choice
        speech = normalize_tts_text(strip_choice_separator(reading))
        source = normalize_tts_text(strip_choice_separator(choice))
        choice_texts.append((speech or source) if keep_all else ("" if speech == source else speech))
    return choice_texts if any(choice_texts) else None


def _optional_speech_text(source_text: str, reading_text: str, rules: list[TtsRule], language: str | None = "ja") -> str | None:
    speech = _speech_text(reading_text, rules, language)
    source = normalize_tts_text(source_text)
    return None if speech == source else speech


def _document_tts_from_reading(
    source_text: str,
    reading_text: str,
    rules: list[TtsRule],
    *,
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
) -> DocumentTts | None:
    reading_text = _apply_field_language_tags(
        source_text,
        reading_text,
        field_key="documentText",
        default_language=language,
        allow_language_tags=allow_language_tags,
        language_settings=language_settings,
    )
    speech = _optional_speech_text(source_text, reading_text, rules, language)
    return DocumentTts(text=speech) if speech else None


def _quiz_tts_from_readings(
    question,
    question_text: str,
    choice_readings: list[str],
    explanation_text: str,
    rules: list[TtsRule],
    *,
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
    learning_language: str | None = None,
    choice_language_mode: str | None = None,
) -> QuizTts | None:
    question_text = _apply_field_language_tags(
        question.question,
        question_text,
        field_key="question",
        default_language=language,
        allow_language_tags=allow_language_tags,
        language_settings=language_settings,
    )
    explanation_text = _apply_field_language_tags(
        question.explanation,
        explanation_text,
        field_key="explanation",
        default_language=language,
        allow_language_tags=allow_language_tags,
        language_settings=language_settings,
    )
    question_text_output = _optional_speech_text(question.question, question_text, rules, language)
    choice_readings = _apply_choice_language_tags(
        question.choices,
        choice_readings,
        default_language=language,
        allow_language_tags=allow_language_tags,
        language_settings=language_settings,
    )
    if (
        allow_language_tags
        and learning_language
        and _base_language(learning_language) != _base_language(language)
        and language_script(learning_language) != language_script(language)
    ):
        learning_script = language_script(learning_language)
        learning_tag = _language_tag(learning_language)
        default_tag = _language_tag(language)
        deterministic_choices: list[str] = []
        for index, source in enumerate(question.choices):
            reading = choice_readings[index] if index < len(choice_readings) else source
            text = _normalize_language_tag_markup(reading or source)
            source_leading_script = leading_script(_strip_language_tags(source))
            ambiguous_han = (
                {learning_script, language_script(language)} == {"japanese", "cjk"}
                and source_leading_script == "cjk"
                and choice_language_mode != "learning"
            )
            if source_leading_script == learning_script and not ambiguous_han:
                text = _strip_edge_default_tags(text, language)
                text = re.sub(rf"^(?:{re.escape(learning_tag)})+", "", text)
                text = f"{learning_tag}{text}"
            elif choice_language_mode == "pack":
                text = re.sub(rf"^(?:{re.escape(default_tag)})+", "", text)
            deterministic_choices.append(text)
        choice_readings = deterministic_choices
    choice_mode, selected_language = _choice_language_mode(language_settings, language)
    keep_all_choices = allow_language_tags and (
        choice_mode == "mixed"
        or (choice_mode == "select" and selected_language and _base_language(selected_language) != _base_language(language))
    )
    choice_texts_output = _sparse_choice_texts(question, choice_readings, keep_all=keep_all_choices)
    explanation_text_output = _optional_speech_text(question.explanation, explanation_text, rules, language)
    if not question_text_output and not choice_texts_output and not explanation_text_output:
        return None
    return QuizTts(
        questionText=question_text_output,
        choiceTexts=choice_texts_output,
        answerText=None,
        explanationText=explanation_text_output,
    )


def _rule_quiz_question_tts(
    question,
    rules: list[TtsRule],
    *,
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
    learning_language: str | None = None,
    choice_language_mode: str | None = None,
) -> QuizTts | None:
    return _quiz_tts_from_readings(
        question,
        question.question,
        [_speech_text(choice, rules, language) for choice in question.choices],
        question.explanation,
        rules,
        language=language,
        allow_language_tags=allow_language_tags,
        language_settings=language_settings,
        learning_language=learning_language,
        choice_language_mode=choice_language_mode,
    )


def _gemini_quiz_question_tts(
    question,
    rules: list[TtsRule],
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
    learning_language: str | None = None,
    choice_language_mode: str | None = None,
) -> QuizTts | None:
    total_chars = len(question.question) + len(question.explanation) + sum(len(choice) for choice in question.choices)
    if total_chars > MAX_TTS_BATCH_CHARS:
        question_text = _gemini_speech_text(question.question, rules, language)
        explanation_text = _gemini_speech_text(question.explanation, rules, language)
        choice_readings = [_gemini_speech_text(choice, rules, language) for choice in question.choices]
        return _quiz_tts_from_readings(
            question,
            question_text,
            choice_readings,
            explanation_text,
            rules,
            language=language,
            allow_language_tags=allow_language_tags,
            language_settings=language_settings,
            learning_language=learning_language,
            choice_language_mode=choice_language_mode,
        )

    data = GeminiClient().generate_json(
        _tts_quiz_question_prompt(question.id, question.question, question.choices, question.explanation, rules, language, allow_language_tags, language_settings)
    )
    data = _llm_response_object_or_raise(data, context="quiz tts")
    question_text = data.get("questionText", "")
    explanation_text = data.get("explanationText", "")
    choices = data.get("choices", [])

    choice_readings = [_speech_text(choice, rules, language) for choice in question.choices]
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            try:
                index = int(choice.get("index"))
            except (TypeError, ValueError):
                continue
            text = choice.get("text", "")
            if 0 <= index < len(choice_readings) and isinstance(text, str) and text.strip():
                choice_readings[index] = _speech_text(text, rules, language)

    if not isinstance(question_text, str) or not question_text.strip():
        question_text = question.question
    if not isinstance(explanation_text, str) or not explanation_text.strip():
        explanation_text = question.explanation

    return _quiz_tts_from_readings(
        question,
        question_text,
        choice_readings,
        explanation_text,
        rules,
        language=language,
        allow_language_tags=allow_language_tags,
        language_settings=language_settings,
        learning_language=learning_language,
        choice_language_mode=choice_language_mode,
    )


def _tts_batch_quiz_prompt(
    questions,
    rules: list[TtsRule],
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
) -> str:
    questions_text = "\n\n".join(
        "\n".join(
            [
                f"- id: {question.id}",
                f"  question: {question.question}",
                "  choices:",
                *[f"    - index: {index}\n      text: {choice}" for index, choice in enumerate(question.choices)],
                f"  explanation: {question.explanation}",
            ]
        )
        for question in questions
    )
    return f"""
Return strict JSON only. Do not use markdown fences.

Create Sokqa TTS reading texts for the fixed quiz questions.

{_tts_reading_rules_block(rules)}

Quiz punctuation rules:
- Keep questionText and explanationText punctuation as natural speech cues. Do not remove sentence-final "?", "？", "!", "！", "." or Japanese punctuation.
- Choice readings are independent tracks. Do not add trailing separator commas to choice texts, but keep meaningful final ".", "?", "？", "!", and "！".
- For English learning phrases, keep the original English text. In multilingual mode, output English spans as [en-US]original English[ja-JP] when returning to Japanese. Bad: [en-US]ウィッチ グリーティング... Good: [en-US]Which greeting is...[ja-JP]

{_language_tag_rules(language, allow_language_tags)}

{_language_policy_block(language_settings, [("questionText", "question"), ("choiceTexts", "choices"), ("explanationText", "explanation")], language)}

Questions:
{questions_text}

Return the same question ids exactly. Do not add, remove, reorder, or rename ids.
Return each choice by its original index.
Return this shape:
{{
  "items": [
    {{
      "id": "question-id",
      "questionText": "tts reading text",
      "choices": [
        {{"index": 0, "text": "tts reading text"}}
      ],
      "explanationText": "tts reading text"
    }}
  ]
}}
""".strip()


def _quiz_tts_from_item(
    question,
    item: dict,
    rules: list[TtsRule],
    *,
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
    learning_language: str | None = None,
    choice_language_mode: str | None = None,
) -> QuizTts | None:
    question_text = item.get("questionText", "")
    explanation_text = item.get("explanationText", "")
    choices = item.get("choices", [])

    choice_readings = [_speech_text(choice, rules, language) for choice in question.choices]
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            try:
                index = int(choice.get("index"))
            except (TypeError, ValueError):
                continue
            text = choice.get("text", "")
            if 0 <= index < len(choice_readings) and isinstance(text, str) and text.strip():
                choice_readings[index] = _speech_text(text, rules, language)

    if not isinstance(question_text, str) or not question_text.strip():
        question_text = question.question
    if not isinstance(explanation_text, str) or not explanation_text.strip():
        explanation_text = question.explanation

    return _quiz_tts_from_readings(
        question,
        question_text,
        choice_readings,
        explanation_text,
        rules,
        language=language,
        allow_language_tags=allow_language_tags,
        language_settings=language_settings,
        learning_language=learning_language,
        choice_language_mode=choice_language_mode,
    )


def _chunk_quiz_questions(questions, max_chars: int = MAX_TTS_BATCH_CHARS):
    entries = [(question.id, _quiz_question_source_text(question)) for question in questions]
    chunks = _chunk_entries(entries, max_chars)
    questions_by_id = {question.id: question for question in questions}
    return [[questions_by_id[entry_id] for entry_id, _ in chunk] for chunk in chunks]


def _gemini_quiz_tts_map(
    questions,
    rules: list[TtsRule],
    language: str = "ja",
    allow_language_tags: bool = False,
    language_settings: TtsLanguageSettings | None = None,
    learning_language: str | None = None,
    choice_language_mode: str | None = None,
) -> dict[str, QuizTts | None]:
    readings: dict[str, QuizTts | None] = {}
    for chunk in _chunk_quiz_questions(questions):
        if len(chunk) == 1 and _quiz_question_char_count(chunk[0]) > MAX_TTS_BATCH_CHARS:
            question = chunk[0]
            readings[question.id] = _gemini_quiz_question_tts(
                question,
                rules,
                language,
                allow_language_tags,
                language_settings,
                learning_language,
                choice_language_mode,
            )
            continue

        data = GeminiClient().generate_json(_tts_batch_quiz_prompt(chunk, rules, language, allow_language_tags, language_settings))
        items = _llm_response_items(data)
        source_by_id = {question.id: question for question in chunk}
        for item in items:
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("id", ""))
            question = source_by_id.get(question_id)
            if question is not None:
                readings[question_id] = _quiz_tts_from_item(
                    question,
                    item,
                    rules,
                    language=language,
                    allow_language_tags=allow_language_tags,
                    language_settings=language_settings,
                    learning_language=learning_language,
                    choice_language_mode=choice_language_mode,
                )
        for question in chunk:
            readings.setdefault(
                question.id,
                _rule_quiz_question_tts(
                    question,
                    rules,
                    language=language,
                    allow_language_tags=allow_language_tags,
                    language_settings=language_settings,
                    learning_language=learning_language,
                    choice_language_mode=choice_language_mode,
                ),
            )
    return readings


def _tts_decision_prompt(kind: str, entries: list[tuple[str, str]], rules: list[TtsRule]) -> str:
    rules_text = "\n".join(f"- {rule.source} -> {rule.reading}" for rule in rules) or "- none"
    entries_text = "\n".join(f"- id: {entry_id}\n  text: {text}" for entry_id, text in entries)
    return f"""
Return strict JSON only. Do not use markdown fences.

Decide which Sokqa {kind} items should receive optional TTS override fields.

Use TTS only when it materially improves speech quality, such as:
- difficult or ambiguous readings
- technical terms, acronyms, commands, product names, English words
- numbers or counters with context-dependent readings
- text that is likely to be mispronounced on mobile TTS engines

Do not select every item by default. Select only useful items.
If a configured rule source appears in an item, include that item because rules must be applied.

Configured pronunciation rules:
{rules_text}

Items:
{entries_text}

Return this shape:
{{
  "ids": ["item-id-that-needs-tts"]
}}
""".strip()


def _gemini_tts_ids(kind: str, entries: list[tuple[str, str]], rules: list[TtsRule]) -> set[str]:
    if not entries or get_settings().gemini_provider != "gemini":
        return set()
    data = GeminiClient().generate_json(_tts_decision_prompt(kind, entries, rules))
    ids = _llm_response_ids(data)
    valid_ids = {entry_id for entry_id, _ in entries}
    return {str(item) for item in ids if str(item) in valid_ids}


def _select_tts_ids(
    kind: str,
    entries: list[tuple[str, str]],
    rules: list[TtsRule],
    allow_gemini: bool = True,
) -> set[str]:
    forced_by_rules = {entry_id for entry_id, text in entries if _has_rule_match(text, rules)}
    if not allow_gemini:
        return forced_by_rules | {entry_id for entry_id, text in entries if _needs_tts_locally(text, rules)}
    try:
        selected = _gemini_tts_ids(kind, entries, rules)
    except Exception:
        selected = {entry_id for entry_id, text in entries if _needs_tts_locally(text, rules)}
    return selected | forced_by_rules


def _mode_or_default(mode: TtsReadingMode | None) -> TtsReadingMode:
    return normalize_tts_reading_mode(mode) or get_settings().tts_reading_mode


def _field_issues(
    file_name: str,
    item_id: str,
    field: str,
    source_text: str,
    tts_text: str,
) -> list[TtsReportItem]:
    issues = _tts_editing_issues(file_name, item_id, field, source_text, tts_text)
    dot_words = re.findall(r"\.[A-Za-z][A-Za-z0-9_-]*", source_text)
    dot_word_index = 0
    for match in re.finditer(r"ドット\s*[ァ-ヶーぁ-ん一-龥]*[A-Za-z][A-Za-z0-9_-]*", tts_text):
        suggested = dot_words[dot_word_index] if dot_word_index < len(dot_words) else None
        dot_word_index += 1
        issues.append(
            TtsReportItem(
                file=file_name,
                itemId=item_id,
                field=field,
                issueType="ascii_after_dot_reading",
                snippet=match.group(0),
                recommendation="未登録のドット始まり語が部分置換されています。llmモードで再生成するか、ユーザー辞書に確定読みを追加してください。",
                suggestedRuleSource=suggested,
            )
        )
    for match in re.finditer(r"\.", tts_text):
        suggested = dot_words[0] if dot_words else None
        previous_char = tts_text[match.start() - 1] if match.start() > 0 else ""
        next_char = tts_text[match.end()] if match.end() < len(tts_text) else ""
        if previous_char.isdigit() and next_char.isdigit():
            continue
        issues.append(
            TtsReportItem(
                file=file_name,
                itemId=item_id,
                field=field,
                issueType="raw_period",
                snippet=tts_text[max(0, match.start() - 12) : match.end() + 12],
                recommendation="読み上げ対象の半角ピリオドが残っています。ドット始まり語の読み、文末句読点、小数点の扱いを確認してください。",
                suggestedRuleSource=suggested,
            )
        )
    for match in re.finditer(r"、{2,}", tts_text):
        issues.append(
            TtsReportItem(
                file=file_name,
                itemId=item_id,
                field=field,
                issueType="duplicate_punctuation",
                snippet=match.group(0),
                recommendation="句読点整形が二重化しています。normalize_tts_text の入力または辞書読みを確認してください。",
                suggestedRuleSource=None,
            )
        )
    return issues


def validate_tts_file(file: GeneratedFile) -> list[TtsReportItem]:
    issues: list[TtsReportItem] = []
    if file.kind == "document":
        pack = SokqaDocumentPack.model_validate(file.content)
        for item in pack.documents:
            if item.tts and item.tts.text:
                issues.extend(_field_issues(file.name, item.id, "text", item.text, item.tts.text))
    elif file.kind == "quiz":
        pack = SokqaQuizPack.model_validate(file.content)
        for question in pack.questions:
            if not question.tts:
                continue
            source_fields = {
                "questionText": question.question,
                "answerText": question.choices[question.answerIndex] if 0 <= question.answerIndex < len(question.choices) else "",
                "explanationText": question.explanation,
            }
            for field_name in ["questionText", "answerText", "explanationText"]:
                value = getattr(question.tts, field_name)
                if value:
                    issues.extend(_field_issues(file.name, question.id, field_name, source_fields[field_name], value))
            if question.tts.choiceTexts:
                for index, value in enumerate(question.tts.choiceTexts):
                    if value:
                        source_choice = question.choices[index] if index < len(question.choices) else ""
                        issues.extend(_field_issues(file.name, question.id, f"choiceTexts.{index}", source_choice, value))
    return issues


def validate_tts_files(files: list[GeneratedFile], mode: TtsReadingMode, llm_ids: list[str] | None = None) -> TtsReport:
    issues: list[TtsReportItem] = []
    for file in files:
        issues.extend(validate_tts_file(file))
    return TtsReport(mode=mode, issues=issues, llmGeneratedIds=sorted(set(llm_ids or [])))


def optimize_document_pack(
    pack: SokqaDocumentPack,
    rules: list[TtsRule],
    mode: TtsReadingMode | None = None,
    llm_ids: list[str] | None = None,
    file_name: str = "",
    warnings: list[TtsReportItem] | None = None,
    language_settings: TtsLanguageSettings | None = None,
) -> SokqaDocumentPack:
    active_mode = _mode_or_default(mode)
    if pack.learningLanguage:
        language_settings = TtsLanguageSettings(
            documentTextLanguageMode="mixed",
            documentTextLanguage=pack.learningLanguage,
        )
    rules = _rules_for_mode(rules, active_mode)
    llm_ids = llm_ids if llm_ids is not None else []
    warnings = warnings if warnings is not None else []
    file_name = file_name or f"{pack.id}.json"
    entries = [(item.id, item.text) for item in pack.documents]
    if active_mode == "none":
        for item in pack.documents:
            item.tags = None
            item.tts = None
        return pack
    selected_ids = (
        {entry_id for entry_id, _ in entries}
        if active_mode in {"llm", "multilingual"}
        else _select_tts_ids("document", entries, rules, allow_gemini=False)
    )
    if not selected_ids:
        for item in pack.documents:
            item.tags = None
            item.tts = None
        return pack
    llm_readings: dict[str, str] = {}
    if active_mode in {"llm", "multilingual"}:
        selected_entries = [(item.id, item.text) for item in pack.documents if item.id in selected_ids]
        try:
            llm_readings = _gemini_document_speech_map(selected_entries, rules, pack.language, active_mode == "multilingual", language_settings)
            llm_ids.extend(entry_id for entry_id, _ in selected_entries)
        except Exception as exc:
            logger.warning("tts_optimizer.llm_document_fallback file=%s error=%s", file_name, exc)
    for item in pack.documents:
        item.tags = None
        if item.id in selected_ids:
            rule_speech = _speech_text(item.text, rules, pack.language)
            if active_mode in {"llm", "multilingual"} and item.id in llm_readings:
                speech = _guard_llm_text(
                    llm_readings[item.id],
                    rule_speech,
                    source_text=item.text,
                    file_name=file_name,
                    item_id=item.id,
                    field="text",
                    warnings=warnings,
                    allow_language_tags=active_mode == "multilingual",
                    allowed_scripts=_allowed_scripts(pack.language, language_settings, "documentText", active_mode == "multilingual"),
                )
            else:
                speech = rule_speech
            item.tts = _document_tts_from_reading(
                item.text,
                speech,
                rules,
                language=pack.language,
                allow_language_tags=active_mode == "multilingual",
                language_settings=language_settings,
            )
        else:
            item.tts = None
    return pack


def optimize_quiz_pack(
    pack: SokqaQuizPack,
    rules: list[TtsRule],
    mode: TtsReadingMode | None = None,
    llm_ids: list[str] | None = None,
    file_name: str = "",
    warnings: list[TtsReportItem] | None = None,
    language_settings: TtsLanguageSettings | None = None,
) -> SokqaQuizPack:
    active_mode = _mode_or_default(mode)
    language_settings = _effective_quiz_language_settings(pack, language_settings)
    rules = _rules_for_mode(rules, active_mode)
    llm_ids = llm_ids if llm_ids is not None else []
    warnings = warnings if warnings is not None else []
    file_name = file_name or f"{pack.id}.json"
    entries = [
        (
            question.id,
            " ".join([question.question, *question.choices, question.explanation]),
        )
        for question in pack.questions
    ]
    if active_mode == "none":
        for question in pack.questions:
            question.tags = None
            question.tts = None
        return pack
    selected_ids = (
        {entry_id for entry_id, _ in entries}
        if active_mode in {"llm", "multilingual"}
        else _select_tts_ids("quiz question", entries, rules, allow_gemini=False)
    )
    llm_readings: dict[str, QuizTts] = {}
    if active_mode in {"llm", "multilingual"}:
        selected_questions = [question for question in pack.questions if question.id in selected_ids]
        try:
            llm_readings = _gemini_quiz_tts_map(
                selected_questions,
                rules,
                pack.language,
                active_mode == "multilingual",
                language_settings,
                pack.learningLanguage,
                pack.choiceLanguageMode,
            )
            llm_ids.extend(question.id for question in selected_questions)
        except Exception as exc:
            logger.warning("tts_optimizer.llm_quiz_fallback file=%s error=%s", file_name, exc)
    for question in pack.questions:
        question.tags = None
        if question.id in selected_ids:
            if active_mode in {"llm", "multilingual"}:
                question.tts = _guard_llm_quiz_tts(
                    question,
                    llm_readings.get(
                        question.id,
                        _rule_quiz_question_tts(
                            question,
                            rules,
                            language=pack.language,
                            allow_language_tags=True,
                            language_settings=language_settings,
                            learning_language=pack.learningLanguage,
                            choice_language_mode=pack.choiceLanguageMode,
                        ),
                    ),
                    rules,
                    file_name=file_name,
                    warnings=warnings,
                    allow_language_tags=active_mode == "multilingual",
                    pack_language=pack.language,
                    language_settings=language_settings,
                )
            else:
                question.tts = _rule_quiz_question_tts(
                    question,
                    rules,
                    language=pack.language,
                    allow_language_tags=False,
                    language_settings=language_settings,
                    learning_language=pack.learningLanguage,
                    choice_language_mode=pack.choiceLanguageMode,
                )
        else:
            question.tts = None
    return pack


def optimize_generated_files_with_report(
    files: list[GeneratedFile],
    rules: list[TtsRule],
    mode: TtsReadingMode | None = None,
    language_settings: TtsLanguageSettings | None = None,
) -> tuple[list[GeneratedFile], TtsReport]:
    active_mode = _mode_or_default(mode)
    optimized = []
    llm_ids: list[str] = []
    warnings: list[TtsReportItem] = []
    for file in files:
        if file.kind == "document":
            pack = optimize_document_pack(SokqaDocumentPack.model_validate(file.content), rules, active_mode, llm_ids, file.name, warnings, language_settings)
            file.content = pack.model_dump(exclude_none=True)
        elif file.kind == "quiz":
            pack = optimize_quiz_pack(SokqaQuizPack.model_validate(file.content), rules, active_mode, llm_ids, file.name, warnings, language_settings)
            file.content = pack.model_dump(exclude_none=True)
        optimized.append(file)
    report = validate_tts_files(optimized, active_mode, llm_ids)
    report.issues.extend(warnings)
    return optimized, report


def optimize_generated_files(
    files: list[GeneratedFile],
    rules: list[TtsRule],
    mode: TtsReadingMode | None = None,
    language_settings: TtsLanguageSettings | None = None,
) -> list[GeneratedFile]:
    optimized, _ = optimize_generated_files_with_report(files, rules, mode, language_settings)
    return optimized
