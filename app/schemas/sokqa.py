from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from app.schemas.common import (
    Difficulty,
    GenerationUnit,
    MaterialMode,
    QuizPurpose,
    ReadingPattern,
    Scale,
    SourceMode,
    StructurePolicy,
    TtsLanguageSettings,
    TtsReadingMode,
    TtsRule,
    normalize_material_mode,
    normalize_structure_policy,
    normalize_tts_reading_mode,
    source_mode_for_material_mode,
    validate_language_code,
)
from app.schemas.pack_v2 import PackManifestV2


def validate_audio_relative_path(value: str | None) -> str | None:
    if value is None:
        return value
    text = value.strip()
    if not text:
        return None
    lowered = text.lower()
    if text.startswith("/") or ".." in text or lowered.startswith(("http://", "https://")):
        raise ValueError("audio path must be relative and must not contain '..' or URL schemes")
    return text


class PlanDocument(BaseModel):
    id: str
    title: str
    goal: str
    keyPoints: list[str] = Field(default_factory=list)
    targetSectionCount: int = Field(default=42, ge=1, le=50)


class PlanQuizPack(BaseModel):
    id: str
    title: str
    purpose: QuizPurpose
    questionCount: int = Field(..., ge=1, le=30)
    difficulty: Difficulty = "standard"
    sourceDocumentIds: list[str] = Field(default_factory=list)


class CoursePlan(BaseModel):
    id: str
    creatorId: str | None = None
    creatorDisplayName: str | None = None
    contentId: str | None = None
    slug: str | None = None
    shortTitle: str | None = None
    title: str
    description: str
    language: str = "ja"
    customInstructions: str | None = Field(default=None, max_length=2000)
    targetUser: str
    difficulty: Difficulty
    scale: Scale | None = None
    author: str = "Sokqa Team"
    version: str = "1.0.0"
    enableTtsOptimize: bool = True
    ttsReadingMode: TtsReadingMode | None = None
    ttsLanguageSettings: TtsLanguageSettings | None = None
    structurePolicy: StructurePolicy = "listening"
    generationUnit: GenerationUnit = "pack"
    docCount: int | None = Field(default=None, ge=0, le=15)
    quizCount: int | None = Field(default=None, ge=0, le=10)
    questionCount: int | None = Field(default=None, ge=1, le=30)
    sectionsPerDocument: int | None = Field(default=None, ge=1, le=50)
    materialMode: MaterialMode = "reference"
    model: str | None = None
    docModel: str | None = None
    quizModel: str | None = None
    plannerModel: str | None = None
    sourceText: str | None = None
    sourceMode: SourceMode | None = None
    globalTags: list[str] = Field(default_factory=list)
    documents: list[PlanDocument]
    quizPacks: list[PlanQuizPack]
    ttsRules: list[TtsRule] = Field(default_factory=list)
    proposedReadingPatterns: list[ReadingPattern] = Field(default_factory=list)
    selectedReadingPatternIds: list[str] = Field(default_factory=list)
    globalTagsMode: Literal["auto", "manual"] = "auto"
    manualGlobalTags: list[str] = Field(default_factory=list)
    descriptionMode: Literal["auto", "manual"] = "auto"
    manualDescription: str | None = None
    descriptionIncludeDate: bool = False
    descriptionIncludeAiDisclaimer: bool = False
    answerPositionMode: Literal["auto", "balanced"] = "balanced"

    @field_validator("ttsReadingMode", mode="before")
    @classmethod
    def normalize_legacy_tts_reading_mode(cls, value):
        return normalize_tts_reading_mode(value)

    @field_validator("materialMode", mode="before")
    @classmethod
    def normalize_legacy_material_mode(cls, value):
        return normalize_material_mode(value) or "reference"

    @field_validator("structurePolicy", mode="before")
    @classmethod
    def normalize_legacy_structure_policy(cls, value):
        return normalize_structure_policy(value)

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language_code(cls, value):
        return validate_language_code(value)

    @model_validator(mode="after")
    def normalize_tts_disabled_mode(self):
        if not self.enableTtsOptimize:
            self.ttsReadingMode = "none"
        if self.sourceText and self.sourceMode and self.materialMode == "reference":
            mapped = normalize_material_mode(self.sourceMode)
            if mapped:
                self.materialMode = mapped
        self.sourceMode = source_mode_for_material_mode(self.materialMode, self.sourceMode) if self.sourceText else None
        return self


class DocumentTts(BaseModel):
    text: str | None = None
    audioUrl: str | None = None
    audioPath: str | None = None
    textLanguage: str | None = None

    @field_validator("audioPath")
    @classmethod
    def audio_path_is_relative(cls, value: str | None) -> str | None:
        return validate_audio_relative_path(value)


class SokqaDocumentItem(BaseModel):
    id: str
    text: str
    tts: DocumentTts | None = None
    tags: list[str] | None = None

    @field_validator("text")
    @classmethod
    def text_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document text must not be empty")
        return value


class SokqaDocumentPack(BaseModel):
    id: str
    type: Literal["document"] = "document"
    schemaVersion: int = 1
    title: str
    description: str = ""
    language: str = "ja"
    author: str | None = None
    assetBaseUrl: str | None = None
    globalTags: list[str] = Field(default_factory=list)
    documents: list[SokqaDocumentItem]


class QuizTts(BaseModel):
    questionText: str | None = None
    choiceTexts: list[str] | None = None
    answerText: str | None = None
    explanationText: str | None = None
    questionAudioUrl: str | None = None
    choiceAudioUrls: list[str | None] | None = None
    explanationAudioUrl: str | None = None
    questionAudioPath: str | None = None
    choiceAudioPaths: list[str | None] | None = None
    explanationAudioPath: str | None = None
    questionLanguage: str | None = None
    choicesLanguage: str | None = None
    answerLanguage: str | None = None
    explanationLanguage: str | None = None

    @field_validator("questionAudioPath", "explanationAudioPath")
    @classmethod
    def audio_path_is_relative(cls, value: str | None) -> str | None:
        return validate_audio_relative_path(value)

    @field_validator("choiceAudioPaths")
    @classmethod
    def choice_audio_paths_are_relative(cls, value: list[str | None] | None) -> list[str | None] | None:
        if value is None:
            return value
        return [validate_audio_relative_path(path) for path in value]


class SokqaQuestion(BaseModel):
    id: str
    question: str
    choices: list[str]
    answerIndex: int
    explanation: str
    tags: list[str] | None = None
    tts: QuizTts | None = None

    @model_validator(mode="after")
    def validate_question(self) -> "SokqaQuestion":
        if not self.question.strip():
            raise ValueError("question must not be empty")
        if len(self.choices) != 4:
            raise ValueError("choices must contain exactly 4 items")
        if len(set(self.choices)) != len(self.choices):
            raise ValueError("choices must not contain exact duplicates")
        if self.answerIndex < 0 or self.answerIndex > 3:
            raise ValueError("answerIndex must be between 0 and 3")
        if not self.explanation.strip():
            raise ValueError("explanation must not be empty")
        return self


class SokqaQuizPack(BaseModel):
    id: str
    type: Literal["quiz"] = "quiz"
    schemaVersion: int = 1
    title: str
    description: str = ""
    language: str = "ja"
    author: str | None = None
    assetBaseUrl: str | None = None
    globalTags: list[str] = Field(default_factory=list)
    questions: list[SokqaQuestion]


class GeneratedFile(BaseModel):
    name: str
    kind: Literal["document", "quiz", "manifest"]
    content: dict[str, Any]
    url: str | None = None


class ValidationErrorItem(BaseModel):
    file: str
    path: str
    message: str
    severity: Literal["error", "warning"] = "error"


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationErrorItem] = Field(default_factory=list)


class TtsReportItem(BaseModel):
    file: str
    itemId: str
    field: str
    issueType: Literal["ascii_after_dot_reading", "raw_period", "duplicate_punctuation", "unexpected_script"]
    snippet: str
    recommendation: str
    suggestedRuleSource: str | None = None


class TtsReport(BaseModel):
    mode: TtsReadingMode
    issues: list[TtsReportItem] = Field(default_factory=list)
    llmGeneratedIds: list[str] = Field(default_factory=list)


class GeneratePackResponse(BaseModel):
    status: Literal["completed"]
    jobId: str
    plan: CoursePlan
    files: list[GeneratedFile]
    manifest: PackManifestV2
    validation: ValidationResult
    ttsReport: TtsReport | None = None
    logs: list[str] = Field(default_factory=list)


class PackRevisionResponse(BaseModel):
    status: Literal["completed"]
    files: list[GeneratedFile]
    manifest: PackManifestV2
    validation: ValidationResult
    ttsReport: TtsReport | None = None
    logs: list[str] = Field(default_factory=list)
