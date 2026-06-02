from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.common import Difficulty, Scale, SourceMode, TtsReadingMode, TtsRule
from app.schemas.sokqa import CoursePlan, GeneratedFile, PackManifest


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
    includeTts: bool = True
    enableTtsOptimize: bool = True
    ttsReadingMode: TtsReadingMode | None = None
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
    ttsReadingMode: TtsReadingMode | None = None
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


class ValidatePackRequest(BaseModel):
    files: list[GeneratedFile] = Field(default_factory=list)
    manifest: PackManifest | None = None


class RepairPackRequest(ValidatePackRequest):
    pass


class OptimizeTtsRequest(BaseModel):
    files: list[GeneratedFile]
    ttsRules: list[TtsRule] = Field(default_factory=list)


class ReviseTtsRequest(BaseModel):
    jobId: str = Field(..., min_length=1)
    ttsRules: list[TtsRule] = Field(default_factory=list)
    persist: bool = True
