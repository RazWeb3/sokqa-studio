import logging
import re

from app.config import get_settings
from app.schemas.common import TtsReadingMode, TtsRule
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
from app.services.tts_text import normalize_tts_text, strip_choice_separator
from app.services.tts_rules import load_system_tts_rules, load_user_tts_rules, merge_tts_rules


MAX_TTS_BATCH_CHARS = 12000
logger = logging.getLogger("sokqa_course_pack_agent")


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
    while True:
        index = value.find(rule.source, cursor)
        if index < 0:
            chunks.append(value[cursor:])
            break
        chunks.append(value[cursor:index])
        if _is_source_inside_existing_reading_parentheses(value, index, rule):
            chunks.append(rule.source)
        else:
            chunks.append(rule.reading)
        cursor = index + source_len
    return "".join(chunks)


def _apply_rule_replacements(value: str, rules: list[TtsRule]) -> str:
    result = value
    for rule in sorted(rules, key=lambda item: len(item.source), reverse=True):
        result = _replace_rule_source(result, rule)
    return result


def _speech_text(value: str, rules: list[TtsRule]) -> str:
    result = _apply_rule_replacements(value, rules)
    return normalize_tts_text(result)


def _is_allowed_tts_char(char: str) -> bool:
    code = ord(char)
    return (
        char in "\t\n\r"
        or 0x0020 <= code <= 0x007E  # basic Latin, digits, and common ASCII symbols
        or 0x3000 <= code <= 0x303F  # Japanese punctuation and ideographic space
        or 0x3040 <= code <= 0x309F  # hiragana
        or 0x30A0 <= code <= 0x30FF  # katakana
        or 0x31F0 <= code <= 0x31FF  # katakana phonetic extensions
        or 0x3400 <= code <= 0x4DBF  # CJK extension A
        or 0x4E00 <= code <= 0x9FFF  # CJK unified ideographs
        or 0xF900 <= code <= 0xFAFF  # CJK compatibility ideographs
        or 0xFF00 <= code <= 0xFFEF  # fullwidth forms and halfwidth katakana
    )


def _unexpected_script_snippet(value: str) -> str | None:
    for index, char in enumerate(value):
        if not _is_allowed_tts_char(char):
            return value[max(0, index - 16) : index + 17]
    return None


def _guard_llm_text(
    value: str | None,
    fallback: str | None,
    *,
    file_name: str,
    item_id: str,
    field: str,
    warnings: list[TtsReportItem],
) -> str | None:
    if not value:
        return value
    snippet = _unexpected_script_snippet(value)
    if not snippet:
        return value
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


def _guard_llm_quiz_tts(
    question,
    tts: QuizTts | None,
    rules: list[TtsRule],
    *,
    file_name: str,
    warnings: list[TtsReportItem],
) -> QuizTts | None:
    if not tts:
        return tts
    fallback = _rule_quiz_question_tts(question, rules)
    fallback_choice_texts = fallback.choiceTexts if fallback and fallback.choiceTexts else None
    question_text = _guard_llm_text(
        tts.questionText,
        fallback.questionText if fallback else None,
        file_name=file_name,
        item_id=question.id,
        field="questionText",
        warnings=warnings,
    )
    explanation_text = _guard_llm_text(
        tts.explanationText,
        fallback.explanationText if fallback else None,
        file_name=file_name,
        item_id=question.id,
        field="explanationText",
        warnings=warnings,
    )
    choice_texts = list(tts.choiceTexts) if tts.choiceTexts else None
    if choice_texts:
        for index, value in enumerate(choice_texts):
            fallback_value = fallback_choice_texts[index] if fallback_choice_texts and index < len(fallback_choice_texts) else ""
            choice_texts[index] = _guard_llm_text(
                value,
                fallback_value,
                file_name=file_name,
                item_id=question.id,
                field=f"choiceTexts.{index}",
                warnings=warnings,
            ) or ""
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


def _has_rule_match(text: str, rules: list[TtsRule]) -> bool:
    if any(rule.source in text for rule in rules):
        return True
    return False


def _needs_tts_locally(text: str, rules: list[TtsRule]) -> bool:
    if _has_rule_match(text, rules):
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
    rules_text = _rules_text(rules)
    return f"""
Return strict JSON only. Do not use markdown fences.

Create a Sokqa TTS reading text for the fixed source text.

Rules:
- Preserve the meaning and sentence order.
- Convert only pronunciation-sensitive terms to readable Japanese/kana where useful.
- A dot is read as "ドット" only when it is immediately followed by an ASCII letter, matching \\.[a-zA-Z].
- Keep the original Japanese punctuation as-is. Do not convert sentence-ending "。" to "、", and do not add or remove punctuation.
- A period "." between digits or inside numbers/codes must stay as the source; do not convert it.
- Do not read dots between digits as "ドット"; for example, 1.2 should be read like "いってんに".
- If an unfamiliar dot-prefixed word or acronym appears, infer a natural katakana reading from the examples.

Contrast examples:
- .gitignore -> ドット ギットイグノア
- 1.2 -> いってんに

Pronunciation examples. Treat these as normative examples, not as the only allowed replacements:
{rules_text}

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
- Preserve the meaning and sentence order.
- Convert only pronunciation-sensitive terms to readable Japanese/kana where useful.
- A dot is read as "ドット" only when it is immediately followed by an ASCII letter, matching \\.[a-zA-Z].
- Keep the original Japanese punctuation as-is. Do not convert sentence-ending "。" to "、", and do not add or remove punctuation.
- A period "." between digits or inside numbers/codes must stay as the source; do not convert it.
- Do not read dots between digits as "ドット"; for example, 1.2 should be read like "いってんに".
- If an unfamiliar dot-prefixed word or acronym appears, infer a natural katakana reading from the examples.

Contrast examples:
- .gitignore -> ドット ギットイグノア
- 1.2 -> いってんに

Pronunciation examples. Treat these as normative examples, not as the only allowed replacements:
{_rules_text(rules)}
""".strip()


def _gemini_speech_text(value: str, rules: list[TtsRule]) -> str:
    data = GeminiClient().generate_json(_tts_reading_prompt(value, rules))
    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return _speech_text(value, rules)
    return _speech_text(text, rules)


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


def _tts_batch_document_prompt(entries: list[tuple[str, str]], rules: list[TtsRule]) -> str:
    entries_text = "\n".join(f"- id: {entry_id}\n  text: {text}" for entry_id, text in entries)
    return f"""
Return strict JSON only. Do not use markdown fences.

Create Sokqa TTS reading texts for the fixed source texts.

{_tts_reading_rules_block(rules)}

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


def _gemini_document_speech_map(entries: list[tuple[str, str]], rules: list[TtsRule]) -> dict[str, str]:
    readings: dict[str, str] = {}
    source_by_id = {entry_id: text for entry_id, text in entries}
    for chunk in _chunk_entries(entries):
        data = GeminiClient().generate_json(_tts_batch_document_prompt(chunk, rules))
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            entry_id = str(item.get("id", ""))
            text = item.get("text", "")
            if entry_id in source_by_id and isinstance(text, str) and text.strip():
                readings[entry_id] = _speech_text(text, rules)
        for entry_id, source_text in chunk:
            readings.setdefault(entry_id, _speech_text(source_text, rules))
    return readings


def _tts_quiz_question_prompt(
    question_id: str,
    question: str,
    choices: list[str],
    explanation: str,
    rules: list[TtsRule],
    language: str = "ja",
) -> str:
    choices_text = "\n".join(f"- index: {index}\n  text: {choice}" for index, choice in enumerate(choices))
    return f"""
Return strict JSON only. Do not use markdown fences.

Create Sokqa TTS reading texts for one fixed quiz question.

{_tts_reading_rules_block(rules)}

Quiz punctuation rules:
- Keep questionText and explanationText punctuation as natural speech cues. Do not remove sentence-final "?", "？", "!", "！", "." or Japanese punctuation.
- Choice readings are independent tracks. Do not add trailing separator commas to choice texts, but keep meaningful final ".", "?", "？", "!", and "！".

Language tag rules:
- The scenario default language is "{language}". Default-language text should not start with a language tag.
- Add a tag such as [ja-JP] or [en-US] only when a span switches to a non-default language.
- Add the default-language tag only when returning from a non-default language to the default language.
- A text item may end while still in a non-default language; the next item starts in the default language automatically.

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


def _sparse_choice_texts(question, choice_readings: list[str]) -> list[str] | None:
    choice_texts: list[str] = []
    for index, choice in enumerate(question.choices):
        reading = choice_readings[index] if index < len(choice_readings) else choice
        speech = normalize_tts_text(strip_choice_separator(reading))
        source = normalize_tts_text(strip_choice_separator(choice))
        choice_texts.append("" if speech == source else speech)
    return choice_texts if any(choice_texts) else None


def _optional_speech_text(source_text: str, reading_text: str, rules: list[TtsRule]) -> str | None:
    speech = _speech_text(reading_text, rules)
    source = normalize_tts_text(source_text)
    return None if speech == source else speech


def _document_tts_from_reading(source_text: str, reading_text: str, rules: list[TtsRule]) -> DocumentTts | None:
    speech = _optional_speech_text(source_text, reading_text, rules)
    return DocumentTts(text=speech) if speech else None


def _quiz_tts_from_readings(
    question,
    question_text: str,
    choice_readings: list[str],
    explanation_text: str,
    rules: list[TtsRule],
) -> QuizTts | None:
    question_text_output = _optional_speech_text(question.question, question_text, rules)
    choice_texts_output = _sparse_choice_texts(question, choice_readings)
    explanation_text_output = _optional_speech_text(question.explanation, explanation_text, rules)
    if not question_text_output and not choice_texts_output and not explanation_text_output:
        return None
    return QuizTts(
        questionText=question_text_output,
        choiceTexts=choice_texts_output,
        answerText=None,
        explanationText=explanation_text_output,
    )


def _rule_quiz_question_tts(question, rules: list[TtsRule]) -> QuizTts | None:
    return _quiz_tts_from_readings(
        question,
        question.question,
        [_speech_text(choice, rules) for choice in question.choices],
        question.explanation,
        rules,
    )


def _gemini_quiz_question_tts(question, rules: list[TtsRule], language: str = "ja") -> QuizTts | None:
    total_chars = len(question.question) + len(question.explanation) + sum(len(choice) for choice in question.choices)
    if total_chars > MAX_TTS_BATCH_CHARS:
        question_text = _gemini_speech_text(question.question, rules)
        explanation_text = _gemini_speech_text(question.explanation, rules)
        choice_readings = [_gemini_speech_text(choice, rules) for choice in question.choices]
        return _quiz_tts_from_readings(question, question_text, choice_readings, explanation_text, rules)

    data = GeminiClient().generate_json(
        _tts_quiz_question_prompt(question.id, question.question, question.choices, question.explanation, rules, language)
    )
    question_text = data.get("questionText", "")
    explanation_text = data.get("explanationText", "")
    choices = data.get("choices", [])

    choice_readings = [_speech_text(choice, rules) for choice in question.choices]
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
                choice_readings[index] = _speech_text(text, rules)

    if not isinstance(question_text, str) or not question_text.strip():
        question_text = question.question
    if not isinstance(explanation_text, str) or not explanation_text.strip():
        explanation_text = question.explanation

    return _quiz_tts_from_readings(question, question_text, choice_readings, explanation_text, rules)


def _tts_batch_quiz_prompt(questions, rules: list[TtsRule], language: str = "ja") -> str:
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

Language tag rules:
- The scenario default language is "{language}". Default-language text should not start with a language tag.
- Add a tag such as [ja-JP] or [en-US] only when a span switches to a non-default language.
- Add the default-language tag only when returning from a non-default language to the default language.
- A text item may end while still in a non-default language; the next item starts in the default language automatically.

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


def _quiz_tts_from_item(question, item: dict, rules: list[TtsRule]) -> QuizTts | None:
    question_text = item.get("questionText", "")
    explanation_text = item.get("explanationText", "")
    choices = item.get("choices", [])

    choice_readings = [_speech_text(choice, rules) for choice in question.choices]
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
                choice_readings[index] = _speech_text(text, rules)

    if not isinstance(question_text, str) or not question_text.strip():
        question_text = question.question
    if not isinstance(explanation_text, str) or not explanation_text.strip():
        explanation_text = question.explanation

    return _quiz_tts_from_readings(question, question_text, choice_readings, explanation_text, rules)


def _chunk_quiz_questions(questions, max_chars: int = MAX_TTS_BATCH_CHARS):
    entries = [(question.id, _quiz_question_source_text(question)) for question in questions]
    chunks = _chunk_entries(entries, max_chars)
    questions_by_id = {question.id: question for question in questions}
    return [[questions_by_id[entry_id] for entry_id, _ in chunk] for chunk in chunks]


def _gemini_quiz_tts_map(questions, rules: list[TtsRule], language: str = "ja") -> dict[str, QuizTts | None]:
    readings: dict[str, QuizTts | None] = {}
    for chunk in _chunk_quiz_questions(questions):
        if len(chunk) == 1 and _quiz_question_char_count(chunk[0]) > MAX_TTS_BATCH_CHARS:
            question = chunk[0]
            readings[question.id] = _gemini_quiz_question_tts(question, rules, language)
            continue

        data = GeminiClient().generate_json(_tts_batch_quiz_prompt(chunk, rules, language))
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        source_by_id = {question.id: question for question in chunk}
        for item in items:
            if not isinstance(item, dict):
                continue
            question_id = str(item.get("id", ""))
            question = source_by_id.get(question_id)
            if question is not None:
                readings[question_id] = _quiz_tts_from_item(question, item, rules)
        for question in chunk:
            readings.setdefault(question.id, _rule_quiz_question_tts(question, rules))
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
    ids = data.get("ids", [])
    if not isinstance(ids, list):
        return set()
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
    return mode or get_settings().tts_reading_mode


def _field_issues(
    file_name: str,
    item_id: str,
    field: str,
    source_text: str,
    tts_text: str,
) -> list[TtsReportItem]:
    issues: list[TtsReportItem] = []
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
                recommendation="未登録のドット始まり語が部分置換されています。llm/autoモードで再生成するか、ユーザー辞書に確定読みを追加してください。",
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
            source = " ".join([question.question, *question.choices, question.explanation])
            for field_name in ["questionText", "answerText", "explanationText"]:
                value = getattr(question.tts, field_name)
                if value:
                    issues.extend(_field_issues(file.name, question.id, field_name, source, value))
            if question.tts.choiceTexts:
                for index, value in enumerate(question.tts.choiceTexts):
                    if value:
                        issues.extend(_field_issues(file.name, question.id, f"choiceTexts.{index}", source, value))
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
) -> SokqaDocumentPack:
    rules = _combined_rules(rules)
    active_mode = _mode_or_default(mode)
    llm_ids = llm_ids if llm_ids is not None else []
    warnings = warnings if warnings is not None else []
    file_name = file_name or f"{pack.id}.json"
    entries = [(item.id, item.text) for item in pack.documents]
    selected_ids = (
        {entry_id for entry_id, _ in entries}
        if active_mode == "llm"
        else _select_tts_ids("document", entries, rules, allow_gemini=False)
    )
    if not selected_ids:
        for item in pack.documents:
            item.tags = None
            item.tts = None
        return pack
    llm_readings: dict[str, str] = {}
    if active_mode == "llm":
        selected_entries = [(item.id, item.text) for item in pack.documents if item.id in selected_ids]
        try:
            llm_readings = _gemini_document_speech_map(selected_entries, rules)
            llm_ids.extend(entry_id for entry_id, _ in selected_entries)
        except Exception as exc:
            logger.warning("tts_optimizer.llm_document_fallback file=%s error=%s", file_name, exc)
    for item in pack.documents:
        item.tags = None
        if item.id in selected_ids:
            rule_speech = _speech_text(item.text, rules)
            if active_mode == "llm" and item.id in llm_readings:
                speech = _guard_llm_text(
                    llm_readings[item.id],
                    rule_speech,
                    file_name=file_name,
                    item_id=item.id,
                    field="text",
                    warnings=warnings,
                )
            else:
                speech = rule_speech
            item.tts = _document_tts_from_reading(item.text, speech, rules)
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
) -> SokqaQuizPack:
    rules = _combined_rules(rules)
    active_mode = _mode_or_default(mode)
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
    selected_ids = (
        {entry_id for entry_id, _ in entries}
        if active_mode == "llm"
        else _select_tts_ids("quiz question", entries, rules, allow_gemini=False)
    )
    llm_readings: dict[str, QuizTts] = {}
    if active_mode == "llm":
        selected_questions = [question for question in pack.questions if question.id in selected_ids]
        try:
            llm_readings = _gemini_quiz_tts_map(selected_questions, rules, pack.language)
            llm_ids.extend(question.id for question in selected_questions)
        except Exception as exc:
            logger.warning("tts_optimizer.llm_quiz_fallback file=%s error=%s", file_name, exc)
    for question in pack.questions:
        question.tags = None
        if question.id in selected_ids:
            if active_mode == "llm":
                question.tts = _guard_llm_quiz_tts(
                    question,
                    llm_readings.get(question.id, _rule_quiz_question_tts(question, rules)),
                    rules,
                    file_name=file_name,
                    warnings=warnings,
                )
            else:
                question.tts = _rule_quiz_question_tts(question, rules)
        else:
            question.tts = None
    return pack


def _rerun_items_with_llm(
    file: GeneratedFile,
    issue_item_ids: set[str],
    rules: list[TtsRule],
    llm_ids: list[str],
    warnings: list[TtsReportItem],
) -> None:
    if not issue_item_ids:
        return
    if file.kind == "document":
        pack = SokqaDocumentPack.model_validate(file.content)
        combined = _combined_rules(rules)
        entries = [(item.id, item.text) for item in pack.documents if item.id in issue_item_ids and item.tts]
        try:
            readings = _gemini_document_speech_map(entries, combined)
            llm_ids.extend(entry_id for entry_id, _ in entries)
        except Exception as exc:
            logger.warning("tts_optimizer.auto_llm_document_fallback file=%s error=%s", file.name, exc)
            readings = {}
        for item in pack.documents:
            if item.id in issue_item_ids and item.tts:
                rule_speech = _speech_text(item.text, combined)
                reading = _guard_llm_text(
                    readings.get(item.id, rule_speech),
                    rule_speech,
                    file_name=file.name,
                    item_id=item.id,
                    field="text",
                    warnings=warnings,
                )
                item.tts = _document_tts_from_reading(item.text, reading, combined)
        file.content = pack.model_dump(exclude_none=True)
    elif file.kind == "quiz":
        pack = SokqaQuizPack.model_validate(file.content)
        combined = _combined_rules(rules)
        selected_questions = [
            question
            for question in pack.questions
            if question.id in issue_item_ids and question.tts
        ]
        try:
            readings = _gemini_quiz_tts_map(selected_questions, combined, pack.language)
            llm_ids.extend(question.id for question in selected_questions)
        except Exception as exc:
            logger.warning("tts_optimizer.auto_llm_quiz_fallback file=%s error=%s", file.name, exc)
            readings = {}
        for question in pack.questions:
            if question.id not in issue_item_ids or not question.tts:
                continue
            question.tts = _guard_llm_quiz_tts(
                question,
                readings.get(question.id, _rule_quiz_question_tts(question, combined)),
                combined,
                file_name=file.name,
                warnings=warnings,
            )
        file.content = pack.model_dump(exclude_none=True)


def optimize_generated_files_with_report(
    files: list[GeneratedFile],
    rules: list[TtsRule],
    mode: TtsReadingMode | None = None,
) -> tuple[list[GeneratedFile], TtsReport]:
    active_mode = _mode_or_default(mode)
    optimized = []
    llm_ids: list[str] = []
    warnings: list[TtsReportItem] = []
    first_pass_mode: TtsReadingMode = "rule" if active_mode == "auto" else active_mode
    for file in files:
        if file.kind == "document":
            pack = optimize_document_pack(SokqaDocumentPack.model_validate(file.content), rules, first_pass_mode, llm_ids, file.name, warnings)
            file.content = pack.model_dump(exclude_none=True)
        elif file.kind == "quiz":
            pack = optimize_quiz_pack(SokqaQuizPack.model_validate(file.content), rules, first_pass_mode, llm_ids, file.name, warnings)
            file.content = pack.model_dump(exclude_none=True)
        optimized.append(file)
    report = validate_tts_files(optimized, active_mode, llm_ids)
    report.issues.extend(warnings)
    if active_mode == "auto" and report.issues:
        issue_ids_by_file: dict[str, set[str]] = {}
        for issue in report.issues:
            if issue.issueType in {"ascii_after_dot_reading", "raw_period"}:
                issue_ids_by_file.setdefault(issue.file, set()).add(issue.itemId)
        for file in optimized:
            _rerun_items_with_llm(file, issue_ids_by_file.get(file.name, set()), rules, llm_ids, warnings)
        report = validate_tts_files(optimized, active_mode, llm_ids)
        report.issues.extend(warnings)
    return optimized, report


def optimize_generated_files(
    files: list[GeneratedFile],
    rules: list[TtsRule],
    mode: TtsReadingMode | None = None,
) -> list[GeneratedFile]:
    optimized, _ = optimize_generated_files_with_report(files, rules, mode)
    return optimized
