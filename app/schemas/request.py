from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import (
    Difficulty,
    Scale,
    SourceMode,
    GenerationUnit,
    MaterialMode,
    StructurePolicy,
    TtsLanguageSettings,
    TtsReadingMode,
    TtsRule,
    normalize_tts_reading_mode,
    validate_language_code,
)
from app.schemas.pack_v2 import PackManifestV2
from app.schemas.sokqa import CoursePlan, GeneratedFile, ValidationResult


class QuizPackSpec(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=120)
    purpose: str
    questionCount: int = Field(..., ge=1, le=100)
    difficulty: Difficulty = "standard"


class PlanPackRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "theme": "ITパスポート試験対策",
                    "targetUser": "IT初心者の社会人・試験直前の学習者",
                    "difficulty": "beginner",
                    "scale": "standard",
                    "language": "ja",
                    "includeTts": True,
                    "documentCount": 10,
                    "quizPacks": [
                        {
                            "id": "quiz_key_concepts",
                            "title": "基礎理解チェック",
                            "purpose": "key_concepts",
                            "questionCount": 30,
                            "difficulty": "beginner",
                        },
                        {
                            "id": "quiz_application",
                            "title": "実践理解チェック",
                            "purpose": "application",
                            "questionCount": 30,
                            "difficulty": "standard",
                        },
                        {
                            "id": "quiz_integrated_review",
                            "title": "総合復習クイズ",
                            "purpose": "integrated_review",
                            "questionCount": 30,
                            "difficulty": "standard",
                        },
                    ],
                    "userTtsRules": [
                        {"source": "IT", "reading": "アイティー"},
                        {"source": "API", "reading": "エーピーアイ"},
                        {"source": "SQL", "reading": "エスキューエル"},
                    ],
                },
                {
                    "theme": "Git基礎講座",
                    "targetUser": "Gitを初めて使う開発者",
                    "difficulty": "beginner",
                    "scale": "standard",
                    "language": "ja",
                    "includeTts": True,
                    "documentCount": 12,
                    "quizPacks": [
                        {
                            "id": "quiz_git_terms",
                            "title": "Git基礎用語チェック",
                            "purpose": "key_concepts",
                            "questionCount": 30,
                            "difficulty": "beginner",
                        },
                        {
                            "id": "quiz_git_workflow",
                            "title": "Git操作理解チェック",
                            "purpose": "application",
                            "questionCount": 30,
                            "difficulty": "standard",
                        },
                        {
                            "id": "quiz_git_review",
                            "title": "Git総合復習クイズ",
                            "purpose": "integrated_review",
                            "questionCount": 30,
                            "difficulty": "standard",
                        },
                    ],
                    "userTtsRules": [
                        {"source": "Git", "reading": "ギット"},
                        {"source": "commit", "reading": "コミット"},
                        {"source": "branch", "reading": "ブランチ"},
                    ],
                },
                {
                    "theme": "日本語初級リスニング",
                    "targetUser": "日本語を学び始めた英語話者",
                    "difficulty": "beginner",
                    "scale": "quick",
                    "language": "en",
                    "includeTts": True,
                    "documentCount": 2,
                    "quizPacks": [
                        {
                            "id": "quiz_meaning",
                            "title": "Meaning Check",
                            "purpose": "key_concepts",
                            "questionCount": 10,
                            "difficulty": "beginner",
                        }
                    ],
                    "userTtsRules": [
                        {"source": "Ohayō gozaimasu", "reading": "[ja-JP]おはようございます"},
                        {"source": "Sumimasen", "reading": "[ja-JP]すみません"},
                    ],
                },
            ]
        }
    )

    theme: str = Field(..., min_length=1, max_length=160)
    targetUser: str = Field(..., min_length=1, max_length=160)
    difficulty: Difficulty = "beginner"
    scale: Scale = "quick"
    language: str = Field(default="ja", min_length=2, max_length=20)
    creatorId: str | None = Field(default=None, min_length=1, max_length=120)
    creatorDisplayName: str | None = Field(default=None, min_length=1, max_length=120)
    contentId: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    includeTts: bool = True
    enableTtsOptimize: bool = True
    ttsReadingMode: TtsReadingMode | None = None
    ttsLanguageSettings: TtsLanguageSettings | None = None
    structurePolicy: StructurePolicy = "standard"
    generationUnit: GenerationUnit = "pack"
    docCount: int | None = Field(default=None, ge=0, le=20)
    quizCount: int | None = Field(default=None, ge=0, le=20)
    materialMode: MaterialMode = "reference"
    model: str | None = Field(default=None, min_length=1, max_length=120)
    docModel: str | None = Field(default=None, min_length=1, max_length=120)
    quizModel: str | None = Field(default=None, min_length=1, max_length=120)
    plannerModel: str | None = Field(default=None, min_length=1, max_length=120)
    documentCount: int | None = Field(default=None, ge=1, le=20)
    sectionsPerDocument: int | None = Field(default=None, ge=1, le=100)
    quizPacks: list[QuizPackSpec] | None = None
    userTtsRules: list[TtsRule] = Field(default_factory=list)
    sourceText: str | None = Field(default=None, max_length=50000)
    sourceMode: SourceMode | None = None

    @field_validator("sourceText")
    @classmethod
    def blank_source_text_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("ttsReadingMode", mode="before")
    @classmethod
    def normalize_legacy_tts_reading_mode(cls, value):
        return normalize_tts_reading_mode(value)

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language_code(cls, value):
        return validate_language_code(value)


class GeneratePackRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "plan": {
                        "id": "it_passport_study_pack",
                        "title": "ITパスポート試験対策 学習パック",
                        "description": "IT初心者の社会人・試験直前の学習者向けのITパスポート試験対策用Sokqa学習パックです。",
                        "language": "ja",
                        "targetUser": "IT初心者の社会人・試験直前の学習者",
                        "difficulty": "beginner",
                        "author": "Sokqa Team",
                        "version": "1.0.0",
                        "documents": [
                            {
                                "id": "doc_01",
                                "title": "ITパスポート試験の全体像",
                                "goal": "試験の目的、出題分野、学習の進め方を理解する",
                                "keyPoints": ["試験概要", "ストラテジ系", "マネジメント系", "テクノロジ系"],
                                "targetSectionCount": 8,
                            },
                            {
                                "id": "doc_02",
                                "title": "情報セキュリティの基礎",
                                "goal": "認証、暗号化、マルウェア対策の基本を理解する",
                                "keyPoints": ["認証", "暗号化", "マルウェア", "リスク管理"],
                                "targetSectionCount": 8,
                            },
                        ],
                        "quizPacks": [
                            {
                                "id": "quiz_key_concepts",
                                "title": "基礎理解チェック",
                                "purpose": "key_concepts",
                                "questionCount": 10,
                                "difficulty": "beginner",
                                "sourceDocumentIds": ["doc_01", "doc_02"],
                            }
                        ],
                        "ttsRules": [
                            {"source": "IT", "reading": "アイティー"},
                            {"source": "AI", "reading": "エーアイ"},
                            {"source": "API", "reading": "エーピーアイ"},
                        ],
                    },
                    "outputMode": "manifest",
                    "persist": True,
                }
            ]
        }
    )

    plan: CoursePlan
    outputMode: Literal["manifest"] = "manifest"
    persist: bool = True
    creatorId: str | None = Field(default=None, min_length=1, max_length=120)
    creatorDisplayName: str | None = Field(default=None, min_length=1, max_length=120)
    contentId: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    ttsReadingMode: TtsReadingMode | None = None
    ttsLanguageSettings: TtsLanguageSettings | None = None
    structurePolicy: StructurePolicy | None = None
    generationUnit: GenerationUnit | None = None
    docCount: int | None = Field(default=None, ge=0, le=20)
    quizCount: int | None = Field(default=None, ge=0, le=20)
    materialMode: MaterialMode | None = None
    sourceText: str | None = Field(default=None, max_length=50000)
    sourceMode: SourceMode | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)
    docModel: str | None = Field(default=None, min_length=1, max_length=120)
    quizModel: str | None = Field(default=None, min_length=1, max_length=120)
    plannerModel: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("sourceText")
    @classmethod
    def blank_source_text_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("ttsReadingMode", mode="before")
    @classmethod
    def normalize_legacy_tts_reading_mode(cls, value):
        return normalize_tts_reading_mode(value)


class ValidatePackRequest(BaseModel):
    files: list[GeneratedFile] = Field(default_factory=list)
    manifest: PackManifestV2 | None = None


class ImportPackInputFile(BaseModel):
    name: str = Field(..., min_length=1, max_length=180)
    content: dict[str, Any]


class ImportPackRequest(BaseModel):
    files: list[ImportPackInputFile] = Field(..., min_length=1)
    creatorId: str | None = Field(default=None, min_length=1, max_length=120)
    creatorDisplayName: str | None = Field(default=None, min_length=1, max_length=120)
    contentId: str | None = Field(default=None, min_length=1, max_length=160)
    slug: str | None = Field(default=None, min_length=1, max_length=160)
    title: str | None = Field(default=None, min_length=1, max_length=160)


class ImportPackResponse(BaseModel):
    status: Literal["imported"] = "imported"
    files: list[GeneratedFile]
    manifest: PackManifestV2
    validation: ValidationResult
    logs: list[str] = Field(default_factory=list)


class DeletePackRequest(BaseModel):
    creatorId: str | None = Field(default=None, min_length=1, max_length=120)
    contentId: str | None = Field(default=None, min_length=1, max_length=160)
    versionId: str | None = Field(default=None, min_length=1, max_length=80)
    manifestUrl: str | None = Field(default=None, min_length=1)
    storagePrefix: str | None = Field(default=None, min_length=1)


class DeletePackResponse(BaseModel):
    status: Literal["deleted"] = "deleted"
    storagePrefix: str
    objectCount: int
    deletedCount: int
    objectNames: list[str] = Field(default_factory=list)


class RepairPackRequest(ValidatePackRequest):
    pass


class OptimizeTtsRequest(BaseModel):
    files: list[GeneratedFile]
    ttsRules: list[TtsRule] = Field(default_factory=list)


class ReviseTtsRequest(BaseModel):
    jobId: str = Field(..., min_length=1)
    ttsRules: list[TtsRule] = Field(default_factory=list)
    persist: bool = True


class TtsRulesConfigResponse(BaseModel):
    rules: list[TtsRule] = Field(default_factory=list)
    path: str


class SaveTtsRulesRequest(BaseModel):
    rules: list[TtsRule] = Field(default_factory=list)


class TtsRecordingTarget(BaseModel):
    manifestUrl: str | None = Field(default=None, min_length=1)
    packUrl: str | None = Field(default=None, min_length=1)
    creatorId: str | None = Field(default=None, min_length=1, max_length=120)
    contentId: str | None = Field(default=None, min_length=1, max_length=160)
    versionId: str | None = Field(default=None, min_length=1, max_length=80)
    packName: str | None = Field(default=None, min_length=1, max_length=160)
    kind: Literal["document", "quiz"] | None = None


class RevisePackTtsRequest(BaseModel):
    target: TtsRecordingTarget
    ttsRules: list[TtsRule] = Field(default_factory=list)
    persist: bool = True


class EstimateTtsRecordingRequest(BaseModel):
    target: TtsRecordingTarget
    unitIds: list[str] | None = None
    textSource: Literal["raw", "corrected"] = "raw"


class RunTtsRecordingRequest(BaseModel):
    target: TtsRecordingTarget
    unitIds: list[str] = Field(..., min_length=1)
    textSource: Literal["raw", "corrected"] = "raw"
    forceRerecord: bool = False
    voiceName: str | None = Field(default=None, min_length=1, max_length=160)
    languageCode: str | None = Field(default=None, min_length=2, max_length=16)
    speakingRate: float | None = Field(default=None, ge=0.25, le=4.0)
    pitch: float | None = Field(default=None, ge=-20.0, le=20.0)


class ResetTtsRecordingRequest(BaseModel):
    target: TtsRecordingTarget
    unitIds: list[str] | None = None
    textSource: Literal["raw", "corrected"] = "raw"
