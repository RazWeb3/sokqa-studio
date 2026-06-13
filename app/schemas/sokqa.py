from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator

from app.schemas.common import Difficulty, QuizPurpose, Scale, SourceMode, TtsReadingMode, TtsRule


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
    targetSectionCount: int = Field(default=8, ge=1, le=120)


class PlanQuizPack(BaseModel):
    id: str
    title: str
    purpose: QuizPurpose
    questionCount: int = Field(..., ge=1, le=100)
    difficulty: Difficulty = "standard"
    sourceDocumentIds: list[str] = Field(default_factory=list)


class CoursePlan(BaseModel):
    id: str
    creatorId: str | None = None
    creatorDisplayName: str | None = None
    contentId: str | None = None
    slug: str | None = None
    title: str
    description: str
    language: str = "ja"
    targetUser: str
    difficulty: Difficulty
    scale: Scale | None = None
    author: str = "Sokqa Team"
    version: str = "1.0.0"
    enableTtsOptimize: bool = True
    ttsReadingMode: TtsReadingMode | None = None
    model: str | None = None
    docModel: str | None = None
    quizModel: str | None = None
    plannerModel: str | None = None
    sourceText: str | None = None
    sourceMode: SourceMode | None = None
    documents: list[PlanDocument]
    quizPacks: list[PlanQuizPack]
    ttsRules: list[TtsRule] = Field(default_factory=list)


class DocumentTts(BaseModel):
    text: str | None = None
    audioUrl: str | None = None
    audioPath: str | None = None
    textLanguage: str | None = None
    ttsNeedsRefresh: bool | None = None

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
    ttsNeedsRefresh: bool | None = None

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


class ManifestItem(BaseModel):
    kind: Literal["document", "quiz"]
    url: str


class ManifestCreator(BaseModel):
    id: str
    displayName: str | None = None


class PackManifest(BaseModel):
    id: str
    type: Literal["pack_manifest"] = "pack_manifest"
    schemaVersion: int = 1
    contentId: str | None = None
    slug: str | None = None
    versionId: str | None = None
    buildId: str | None = None
    generatedAt: str | None = None
    creator: ManifestCreator | None = None
    title: str | None = None
    description: str = ""
    language: str = "ja"
    author: str | None = "Sokqa Team"
    version: str | None = None
    scale: Scale | None = None
    globalTags: list[str] = Field(default_factory=list)
    items: list[ManifestItem]


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
    issueType: Literal["ascii_after_dot_reading", "raw_period", "duplicate_punctuation"]
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
    manifest: PackManifest
    validation: ValidationResult
    ttsReport: TtsReport | None = None
    logs: list[str] = Field(default_factory=list)
