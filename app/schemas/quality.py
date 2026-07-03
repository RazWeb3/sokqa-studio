from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.request import TtsRecordingTarget


QualityCategory = Literal["factual", "reading", "double_utterance", "notation", "style", "leak", "tts_text_mismatch"]
QualitySeverity = Literal["high", "medium", "low"]


class QualityLocation(BaseModel):
    fileName: str = Field(..., min_length=1)
    unitId: str | None = None
    field: str | None = None


class QualityIssue(BaseModel):
    category: QualityCategory
    severity: QualitySeverity
    confidence: float = Field(..., ge=0.0, le=1.0)
    location: QualityLocation
    excerpt: str = Field(..., min_length=1)
    issue: str = Field(..., min_length=1)
    suggestion: str = Field(..., min_length=1)
    original: str | None = None


class QualityCheckRequest(BaseModel):
    target: TtsRecordingTarget
    maxIssues: int = Field(default=50, ge=1, le=100)


class QualityCheckResponse(BaseModel):
    fileName: str
    model: str
    issues: list[QualityIssue] = Field(default_factory=list)
    truncated: bool = False
