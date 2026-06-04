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
from app.services.tts_text import normalize_tts_text, strip_terminal_punctuation
from app.services.tts_rules import load_system_tts_rules, load_user_tts_rules, merge_tts_rules


MAX_TTS_BATCH_CHARS = 12000


def _apply_rule_replacements(value: str, rules: list[TtsRule]) -> str:
    result = value
    for rule in sorted(rules, key=lambda item: len(item.source), reverse=True):
        result = result.replace(rule.source, rule.reading)
    return result


def _speech_text(value: str, rules: list[TtsRule]) -> str:
    result = _apply_rule_replacements(value, rules)
    return normalize_tts_text(result)


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


def _tts_quiz_question_prompt(question_id: str, question: str, choices: list[str], explanation: str, rules: list[TtsRule]) -> str:
    choices_text = "\n".join(f"- index: {index}\n  text: {choice}" for index, choice in enumerate(choices))
    return f"""
Return strict JSON only. Do not use markdown fences.

Create Sokqa TTS reading texts for one fixed quiz question.

{_tts_reading_rules_block(rules)}

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


def _quiz_tts_from_readings(
    question,
    question_text: str,
    choice_readings: list[str],
    explanation_text: str,
    rules: list[TtsRule],
) -> QuizTts:
    choices_text = "".join(
        f"{strip_terminal_punctuation(reading)}、"
        for reading in choice_readings
    )
    answer_text = f"正解は、{strip_terminal_punctuation(choice_readings[question.answerIndex])}"
    return QuizTts(
        questionText=_speech_text(question_text, rules),
        choicesText=normalize_tts_text(choices_text),
        answerText=normalize_tts_text(answer_text),
        explanationText=_speech_text(explanation_text, rules),
    )


def _rule_quiz_question_tts(question, rules: list[TtsRule]) -> QuizTts:
    return _quiz_tts_from_readings(
        question,
        question.question,
        [_speech_text(choice, rules) for choice in question.choices],
        question.explanation,
        rules,
    )


def _gemini_quiz_question_tts(question, rules: list[TtsRule]) -> QuizTts:
    total_chars = len(question.question) + len(question.explanation) + sum(len(choice) for choice in question.choices)
    if total_chars > MAX_TTS_BATCH_CHARS:
        question_text = _gemini_speech_text(question.question, rules)
        explanation_text = _gemini_speech_text(question.explanation, rules)
        choice_readings = [_gemini_speech_text(choice, rules) for choice in question.choices]
        return _quiz_tts_from_readings(question, question_text, choice_readings, explanation_text, rules)

    data = GeminiClient().generate_json(
        _tts_quiz_question_prompt(question.id, question.question, question.choices, question.explanation, rules)
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


def _tts_batch_quiz_prompt(questions, rules: list[TtsRule]) -> str:
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


def _quiz_tts_from_item(question, item: dict, rules: list[TtsRule]) -> QuizTts:
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


def _gemini_quiz_tts_map(questions, rules: list[TtsRule]) -> dict[str, QuizTts]:
    readings: dict[str, QuizTts] = {}
    for chunk in _chunk_quiz_questions(questions):
        if len(chunk) == 1 and _quiz_question_char_count(chunk[0]) > MAX_TTS_BATCH_CHARS:
            question = chunk[0]
            readings[question.id] = _gemini_quiz_question_tts(question, rules)
            continue

        data = GeminiClient().generate_json(_tts_batch_quiz_prompt(chunk, rules))
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
            for field_name in ["questionText", "choicesText", "answerText", "explanationText"]:
                value = getattr(question.tts, field_name)
                if value:
                    issues.extend(_field_issues(file.name, question.id, field_name, source, value))
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
) -> SokqaDocumentPack:
    rules = _combined_rules(rules)
    active_mode = _mode_or_default(mode)
    llm_ids = llm_ids if llm_ids is not None else []
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
        llm_readings = _gemini_document_speech_map(selected_entries, rules)
        llm_ids.extend(entry_id for entry_id, _ in selected_entries)
    for item in pack.documents:
        item.tags = None
        if item.id in selected_ids:
            speech = llm_readings.get(item.id, _speech_text(item.text, rules)) if active_mode == "llm" else _speech_text(item.text, rules)
            item.tts = DocumentTts(text=speech)
        else:
            item.tts = None
    return pack


def optimize_quiz_pack(
    pack: SokqaQuizPack,
    rules: list[TtsRule],
    mode: TtsReadingMode | None = None,
    llm_ids: list[str] | None = None,
) -> SokqaQuizPack:
    rules = _combined_rules(rules)
    active_mode = _mode_or_default(mode)
    llm_ids = llm_ids if llm_ids is not None else []
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
        llm_readings = _gemini_quiz_tts_map(selected_questions, rules)
        llm_ids.extend(question.id for question in selected_questions)
    for question in pack.questions:
        question.tags = None
        if question.id in selected_ids:
            if active_mode == "llm":
                question.tts = llm_readings.get(question.id, _rule_quiz_question_tts(question, rules))
            else:
                question.tts = _rule_quiz_question_tts(question, rules)
        else:
            question.tts = None
    return pack


def _rerun_items_with_llm(file: GeneratedFile, issue_item_ids: set[str], rules: list[TtsRule], llm_ids: list[str]) -> None:
    if not issue_item_ids:
        return
    if file.kind == "document":
        pack = SokqaDocumentPack.model_validate(file.content)
        combined = _combined_rules(rules)
        entries = [(item.id, item.text) for item in pack.documents if item.id in issue_item_ids and item.tts]
        readings = _gemini_document_speech_map(entries, combined)
        llm_ids.extend(entry_id for entry_id, _ in entries)
        for item in pack.documents:
            if item.id in issue_item_ids and item.tts:
                item.tts = DocumentTts(text=readings.get(item.id, _speech_text(item.text, combined)))
        file.content = pack.model_dump(exclude_none=True)
    elif file.kind == "quiz":
        pack = SokqaQuizPack.model_validate(file.content)
        combined = _combined_rules(rules)
        selected_questions = [
            question
            for question in pack.questions
            if question.id in issue_item_ids and question.tts
        ]
        readings = _gemini_quiz_tts_map(selected_questions, combined)
        llm_ids.extend(question.id for question in selected_questions)
        for question in pack.questions:
            if question.id not in issue_item_ids or not question.tts:
                continue
            question.tts = readings.get(question.id, _rule_quiz_question_tts(question, combined))
        file.content = pack.model_dump(exclude_none=True)


def optimize_generated_files_with_report(
    files: list[GeneratedFile],
    rules: list[TtsRule],
    mode: TtsReadingMode | None = None,
) -> tuple[list[GeneratedFile], TtsReport]:
    active_mode = _mode_or_default(mode)
    optimized = []
    llm_ids: list[str] = []
    first_pass_mode: TtsReadingMode = "rule" if active_mode == "auto" else active_mode
    for file in files:
        if file.kind == "document":
            pack = optimize_document_pack(SokqaDocumentPack.model_validate(file.content), rules, first_pass_mode, llm_ids)
            file.content = pack.model_dump(exclude_none=True)
        elif file.kind == "quiz":
            pack = optimize_quiz_pack(SokqaQuizPack.model_validate(file.content), rules, first_pass_mode, llm_ids)
            file.content = pack.model_dump(exclude_none=True)
        optimized.append(file)
    report = validate_tts_files(optimized, active_mode, llm_ids)
    if active_mode == "auto" and report.issues:
        issue_ids_by_file: dict[str, set[str]] = {}
        for issue in report.issues:
            if issue.issueType in {"ascii_after_dot_reading", "raw_period"}:
                issue_ids_by_file.setdefault(issue.file, set()).add(issue.itemId)
        for file in optimized:
            _rerun_items_with_llm(file, issue_ids_by_file.get(file.name, set()), rules, llm_ids)
        report = validate_tts_files(optimized, active_mode, llm_ids)
    return optimized, report


def optimize_generated_files(
    files: list[GeneratedFile],
    rules: list[TtsRule],
    mode: TtsReadingMode | None = None,
) -> list[GeneratedFile]:
    optimized, _ = optimize_generated_files_with_report(files, rules, mode)
    return optimized
