import re

from app.schemas.common import TtsRule
from app.schemas.sokqa import (
    DocumentTts,
    GeneratedFile,
    QuizTts,
    SokqaDocumentPack,
    SokqaQuizPack,
)


def _speech_text(value: str, rules: list[TtsRule]) -> str:
    result = value
    for rule in rules:
        result = result.replace(rule.source, rule.reading)
    result = re.sub(r"[。．.]", "、", result)
    result = re.sub(r"\s+", " ", result).strip()
    return result


def optimize_document_pack(pack: SokqaDocumentPack, rules: list[TtsRule]) -> SokqaDocumentPack:
    if not rules:
        return pack
    for item in pack.documents:
        speech = _speech_text(item.text, rules)
        if speech != item.text:
            item.tts = DocumentTts(text=speech)
    return pack


def optimize_quiz_pack(pack: SokqaQuizPack, rules: list[TtsRule]) -> SokqaQuizPack:
    if not rules:
        return pack
    for question in pack.questions:
        question_text = _speech_text(question.question, rules)
        explanation_text = _speech_text(question.explanation, rules)
        choices_text = "、".join([f"{index + 1}番、{_speech_text(choice, rules)}" for index, choice in enumerate(question.choices)])
        answer_text = f"正解は{question.answerIndex + 1}番、{_speech_text(question.choices[question.answerIndex], rules)}"
        if (
            question_text != question.question
            or explanation_text != question.explanation
            or any(_speech_text(choice, rules) != choice for choice in question.choices)
        ):
            question.tts = QuizTts(
                questionText=question_text,
                choicesText=choices_text,
                answerText=answer_text,
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
