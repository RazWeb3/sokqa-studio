import re

from app.config import get_settings
from app.schemas.common import TtsRule
from app.schemas.sokqa import (
    DocumentTts,
    GeneratedFile,
    QuizTts,
    SokqaDocumentPack,
    SokqaQuizPack,
)
from app.services.gemini_client import GeminiClient
from app.services.tts_text import normalize_tts_text, strip_terminal_punctuation
from app.services.tts_rules import load_system_tts_rules, load_user_tts_rules, merge_tts_rules


def _speech_text(value: str, rules: list[TtsRule]) -> str:
    result = value
    for rule in rules:
        result = result.replace(rule.source, rule.reading)
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
    if re.search(r"[A-Z]{2,}", text):
        return True
    return False


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


def _select_tts_ids(kind: str, entries: list[tuple[str, str]], rules: list[TtsRule]) -> set[str]:
    forced_by_rules = {entry_id for entry_id, text in entries if _has_rule_match(text, rules)}
    try:
        selected = _gemini_tts_ids(kind, entries, rules)
    except Exception:
        selected = {entry_id for entry_id, text in entries if _needs_tts_locally(text, rules)}
    return selected | forced_by_rules


def optimize_document_pack(pack: SokqaDocumentPack, rules: list[TtsRule]) -> SokqaDocumentPack:
    rules = _combined_rules(rules)
    entries = [(item.id, item.text) for item in pack.documents]
    selected_ids = _select_tts_ids("document", entries, rules)
    if not selected_ids:
        for item in pack.documents:
            item.tags = None
            item.tts = None
        return pack
    for item in pack.documents:
        item.tags = None
        if item.id in selected_ids:
            speech = _speech_text(item.text, rules)
            item.tts = DocumentTts(text=speech)
        else:
            item.tts = None
    return pack


def optimize_quiz_pack(pack: SokqaQuizPack, rules: list[TtsRule]) -> SokqaQuizPack:
    rules = _combined_rules(rules)
    entries = [
        (
            question.id,
            " ".join([question.question, *question.choices, question.explanation]),
        )
        for question in pack.questions
    ]
    selected_ids = _select_tts_ids("quiz question", entries, rules)
    for question in pack.questions:
        question.tags = None
        if question.id in selected_ids:
            question_text = _speech_text(question.question, rules)
            explanation_text = _speech_text(question.explanation, rules)
            choices_text = "".join(
                f"{index + 1}番、{strip_terminal_punctuation(_speech_text(choice, rules))}、"
                for index, choice in enumerate(question.choices)
            )
            answer_text = f"正解は{question.answerIndex + 1}番、{strip_terminal_punctuation(_speech_text(question.choices[question.answerIndex], rules))}"
            question.tts = QuizTts(
                questionText=question_text,
                choicesText=normalize_tts_text(choices_text),
                answerText=normalize_tts_text(answer_text),
                explanationText=explanation_text,
            )
        else:
            question.tts = None
    return pack


def optimize_generated_files(files: list[GeneratedFile], rules: list[TtsRule]) -> list[GeneratedFile]:
    optimized = []
    for file in files:
        if file.kind == "document":
            pack = optimize_document_pack(SokqaDocumentPack.model_validate(file.content), rules)
            file.content = pack.model_dump(exclude_none=True)
        elif file.kind == "quiz":
            pack = optimize_quiz_pack(SokqaQuizPack.model_validate(file.content), rules)
            file.content = pack.model_dump(exclude_none=True)
        optimized.append(file)
    return optimized
