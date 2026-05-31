import re

from app.schemas.common import TtsRule
from app.schemas.sokqa import (
    DocumentTts,
    GeneratedFile,
    QuizTts,
    SokqaDocumentPack,
    SokqaQuizPack,
)
from app.services.tts_text import normalize_tts_text, strip_terminal_punctuation
from app.services.tts_rules import load_configured_tts_rules


def _speech_text(value: str, rules: list[TtsRule]) -> str:
    result = value
    for rule in rules:
        result = result.replace(rule.source, rule.reading)
    return normalize_tts_text(result)


def _combined_rules(rules: list[TtsRule]) -> list[TtsRule]:
    configured = load_configured_tts_rules()
    return [*configured, *rules]


def _needs_document_tts(text: str, rules: list[TtsRule]) -> bool:
    if any(rule.source in text for rule in rules):
        return True
    risky_markers = ["API", "AI", "UI", "UX", "SQL", "JSON", "CPU", "PC", "URL", "1時", "9時", "20歳"]
    if any(marker in text for marker in risky_markers):
        return True
    if re.search(r"`[^`]+`", text):
        return True
    if re.search(r"[A-Z]{2,}", text):
        return True
    return False


def optimize_document_pack(pack: SokqaDocumentPack, rules: list[TtsRule]) -> SokqaDocumentPack:
    rules = _combined_rules(rules)
    if not rules and not any(_needs_document_tts(item.text, []) for item in pack.documents):
        return pack
    for item in pack.documents:
        item.tags = None
        if _needs_document_tts(item.text, rules):
            speech = _speech_text(item.text, rules)
            item.tts = DocumentTts(text=speech)
        else:
            item.tts = None
    return pack


def optimize_quiz_pack(pack: SokqaQuizPack, rules: list[TtsRule]) -> SokqaQuizPack:
    rules = _combined_rules(rules)
    for question in pack.questions:
        question.tags = None
        question_text = _speech_text(question.question, rules)
        explanation_text = _speech_text(question.explanation, rules)
        choices_text = "".join(
            f"{index + 1}番、{strip_terminal_punctuation(_speech_text(choice, rules))}、"
            for index, choice in enumerate(question.choices)
        )
        answer_text = f"正解は{question.answerIndex + 1}番、{strip_terminal_punctuation(_speech_text(question.choices[question.answerIndex], rules))}"
        if (
            question_text != question.question
            or explanation_text != question.explanation
            or any(_speech_text(choice, rules) != choice for choice in question.choices)
        ):
            question.tts = QuizTts(
                questionText=question_text,
                choicesText=normalize_tts_text(choices_text),
                answerText=normalize_tts_text(answer_text),
                explanationText=explanation_text,
            )
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
