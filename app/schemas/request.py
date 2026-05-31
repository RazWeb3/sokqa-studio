from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import Difficulty, Scale, TtsRule
from app.schemas.sokqa import CoursePlan, GeneratedFile, PackManifest


class QuizPackSpec(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    title: str = Field(..., min_length=1, max_length=120)
    purpose: str
    questionCount: int = Field(..., ge=1, le=100)
    difficulty: Difficulty = "standard"


class PlanPackRequest(BaseModel):
    theme: str = Field(..., min_length=1, max_length=160)
    targetUser: str = Field(..., min_length=1, max_length=160)
    difficulty: Difficulty = "beginner"
    scale: Scale = "quick"
    language: str = Field(default="ja", min_length=2, max_length=20)
    includeTts: bool = True
    documentCount: int | None = Field(default=None, ge=1, le=20)
    quizPacks: list[QuizPackSpec] | None = None
    userTtsRules: list[TtsRule] = Field(default_factory=list)


class GeneratePackRequest(BaseModel):
    plan: CoursePlan
    outputMode: Literal["manifest"] = "manifest"
    persist: bool = True


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
