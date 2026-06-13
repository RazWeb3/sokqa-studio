from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.quality import QualityCategory, QualityIssue, QualityLocation
from app.schemas.request import TtsRecordingTarget


AutoFixCategory = Literal["reading", "double_utterance", "notation", "tts_text_mismatch"]
PendingFixCategory = Literal["factual", "style", "leak"]


class AppliedFix(BaseModel):
    id: str = Field(..., min_length=1)
    category: AutoFixCategory
    location: QualityLocation
    field: str = Field(..., min_length=1)
    before: str | None = ""
    after: str
    sourceIssue: str = Field(..., min_length=1)


class PendingFix(BaseModel):
    id: str = Field(..., min_length=1)
    category: PendingFixCategory
    location: QualityLocation
    field: str = Field(..., min_length=1)
    before: str | None = ""
    suggestedAfter: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    sourceIssue: str = Field(..., min_length=1)


class UnappliedFix(BaseModel):
    id: str = Field(..., min_length=1)
    category: AutoFixCategory
    location: QualityLocation
    field: str = Field(..., min_length=1)
    before: str | None = ""
    suggestion: str | None = None
    reason: str = Field(..., min_length=1)
    sourceIssue: str = Field(..., min_length=1)


class QualityFixRequest(BaseModel):
    target: TtsRecordingTarget
    issues: list[QualityIssue] = Field(default_factory=list)
    maxFixes: int = Field(default=50, ge=1, le=100)


class QualityFixResponse(BaseModel):
    fileName: str
    model: str
    appliedFixes: list[AppliedFix] = Field(default_factory=list)
    pendingFixes: list[PendingFix] = Field(default_factory=list)
    unappliedFixes: list[UnappliedFix] = Field(default_factory=list)
    updatedJson: dict[str, Any]
    reRecordNeededUnits: list["ReRecordNeededUnit"] = Field(default_factory=list)
    truncated: bool = False


class QualityFixApplyRequest(BaseModel):
    updatedJson: dict[str, Any]
    pendingFixes: list[PendingFix] = Field(default_factory=list)
    approvedIds: list[str] = Field(default_factory=list)


class QualityFixApplyResponse(BaseModel):
    finalJson: dict[str, Any]
    appliedApprovedIds: list[str] = Field(default_factory=list)
    skippedIds: list[str] = Field(default_factory=list)


class QualityFixSaveFile(BaseModel):
    name: str = Field(..., min_length=1, max_length=180)
    kind: Literal["document", "quiz"]
    content: dict[str, Any]


class QualityFixSaveRequest(BaseModel):
    target: TtsRecordingTarget
    files: list[QualityFixSaveFile] = Field(..., min_length=1)
    appliedFixes: list[AppliedFix] = Field(default_factory=list)


class ReRecordNeededUnit(BaseModel):
    fileName: str
    unitId: str | None = None
    field: str | None = None
    category: AutoFixCategory


class QualityFixSaveResponse(BaseModel):
    newVersionId: str
    newAssetBaseUrl: str
    storagePrefix: str
    files: list[QualityFixSaveFile]
    reRecordNeededUnits: list[ReRecordNeededUnit] = Field(default_factory=list)
