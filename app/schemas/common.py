import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator


Scale = Literal["quick", "standard", "auto"]
Difficulty = Literal["beginner", "standard", "advanced"]
QuizPurpose = Literal["key_concepts", "application", "integrated_review", "custom"]
TtsReadingMode = Literal["none", "rule", "llm", "multilingual"]
TtsLanguageMode = Literal["auto", "mixed", "select"]
SourceMode = Literal["document_only", "document_reference"]
StructurePolicy = Literal["standard", "listening", "sequential"]
GenerationUnit = Literal["document", "quiz", "pack"]
MaterialMode = Literal["reference", "strict"]

SUPPORTED_PACK_LANGUAGES = {
    "ja": "ja-JP",
    "en": "en-US",
    "zh": "zh-CN",
    "ko": "ko-KR",
    "es": "es-ES",
    "fr": "fr-FR",
    "de": "de-DE",
    "it": "it-IT",
    "pt": "pt-PT",
    "id": "id-ID",
}


def validate_language_code(value: str | None) -> str | None:
    if value is None:
        return value
    text = value.strip()
    if not re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*", text):
        raise ValueError("language must be a BCP-47-like code such as ja, en, ko, pt-BR, or es-419")
    parts = text.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        normalized.append(part.upper() if len(part) == 2 and part.isalpha() else part)
    return "-".join(normalized)


def default_speech_language_code(language: str | None) -> str:
    normalized = validate_language_code(language or "ja") or "ja"
    base = normalized.split("-")[0].lower()
    return SUPPORTED_PACK_LANGUAGES.get(base, normalized)


def normalize_tts_reading_mode(value):
    if value in (None, ""):
        return None
    if value == "auto":
        return "llm"
    return value


class TtsRule(BaseModel):
    source: str = Field(..., min_length=1, max_length=80)
    reading: str = Field(..., min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=200)


class TtsLanguageSettings(BaseModel):
    documentTextLanguageMode: TtsLanguageMode = "auto"
    documentTextLanguage: str | None = None
    questionLanguageMode: TtsLanguageMode = "auto"
    questionLanguage: str | None = None
    choicesLanguageMode: TtsLanguageMode = "auto"
    choicesLanguage: str | None = None
    explanationLanguageMode: TtsLanguageMode = "auto"
    explanationLanguage: str | None = None

    @field_validator(
        "documentTextLanguage",
        "questionLanguage",
        "choicesLanguage",
        "explanationLanguage",
        mode="before",
    )
    @classmethod
    def normalize_language_code(cls, value):
        if value in (None, ""):
            return None
        return validate_language_code(value)


class ReadingPattern(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=400)
    examples: list[str] = Field(default_factory=list)
    recommended: bool = False
