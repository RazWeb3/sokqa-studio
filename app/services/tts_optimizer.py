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
    rules_text = "\n".join(f"- {rule.source} -> {rule.reading}" for rule in rules) or "- none"
    return f"""
Return strict JSON only. Do not use markdown fences.

Create a Sokqa TTS reading text for the fixed source text.

Rules:
- Preserve the meaning and sentence order.
- Convert only pronunciation-sensitive terms to readable Japanese/kana where useful.
- A dot is read as "ドット" only when it is immediately followed by an ASCII letter, matching \\.[a-zA-Z].
- Do not read sentence periods or punctuation separators as "ドット"; normalize sentence endings "。" and "." to "、".
- Do not read dots between digits as "ドット"; for example, 1.2 should be read like "いってんに".
- If an unfamiliar dot-prefixed word or acronym appears, infer a natural katakana reading from the examples.

Contrast examples:
- .gitignore -> ドット ギットイグノア
- 〜します。 -> 〜します、
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


def _gemini_speech_text(value: str, rules: list[TtsRule]) -> str:
    data = GeminiClient().generate_json(_tts_reading_prompt(value, rules))
    text = data.get("text", "")
    if not isinstance(text, str) or not text.strip():
        return _speech_text(value, rules)
    return _speech_text(text, rules)


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


def _document_speech(item_id: str, text: str, rules: list[TtsRule], mode: TtsReadingMode, llm_ids: list[str]) -> str:
    if mode == "llm":
        llm_ids.append(item_id)
        return _gemini_speech_text(text, rules)
    return _speech_text(text, rules)


def _quiz_speech(item_id: str, text: str, rules: list[TtsRule], mode: TtsReadingMode, llm_ids: list[str]) -> str:
    if mode == "llm":
        llm_ids.append(item_id)
        return _gemini_speech_text(text, rules)
    return _speech_text(text, rules)


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
    for item in pack.documents:
        item.tags = None
        if item.id in selected_ids:
            speech = _document_speech(item.id, item.text, rules, active_mode, llm_ids)
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
    for question in pack.questions:
        question.tags = None
        if question.id in selected_ids:
            question_key = f"{question.id}:question"
            explanation_key = f"{question.id}:explanation"
            question_text = _quiz_speech(question_key, question.question, rules, active_mode, llm_ids)
            explanation_text = _quiz_speech(explanation_key, question.explanation, rules, active_mode, llm_ids)
            choices_text = "".join(
                f"{index + 1}番、{strip_terminal_punctuation(_quiz_speech(f'{question.id}:choice:{index}', choice, rules, active_mode, llm_ids))}、"
                for index, choice in enumerate(question.choices)
            )
            answer_text = f"正解は{question.answerIndex + 1}番、{strip_terminal_punctuation(_quiz_speech(f'{question.id}:answer', question.choices[question.answerIndex], rules, active_mode, llm_ids))}"
            question.tts = QuizTts(
                questionText=question_text,
                choicesText=normalize_tts_text(choices_text),
                answerText=normalize_tts_text(answer_text),
                explanationText=explanation_text,
            )
        else:
            question.tts = None
    return pack


def _rerun_items_with_llm(file: GeneratedFile, issue_item_ids: set[str], rules: list[TtsRule], llm_ids: list[str]) -> None:
    if not issue_item_ids:
        return
    if file.kind == "document":
        pack = SokqaDocumentPack.model_validate(file.content)
        combined = _combined_rules(rules)
        for item in pack.documents:
            if item.id in issue_item_ids and item.tts:
                llm_ids.append(item.id)
                item.tts = DocumentTts(text=_gemini_speech_text(item.text, combined))
        file.content = pack.model_dump(exclude_none=True)
    elif file.kind == "quiz":
        pack = SokqaQuizPack.model_validate(file.content)
        combined = _combined_rules(rules)
        for question in pack.questions:
            if question.id not in issue_item_ids or not question.tts:
                continue
            llm_ids.append(question.id)
            question.tts = QuizTts(
                questionText=_gemini_speech_text(question.question, combined),
                choicesText=normalize_tts_text(
                    "".join(
                        f"{index + 1}番、{strip_terminal_punctuation(_gemini_speech_text(choice, combined))}、"
                        for index, choice in enumerate(question.choices)
                    )
                ),
                answerText=normalize_tts_text(
                    f"正解は{question.answerIndex + 1}番、{strip_terminal_punctuation(_gemini_speech_text(question.choices[question.answerIndex], combined))}"
                ),
                explanationText=_gemini_speech_text(question.explanation, combined),
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
