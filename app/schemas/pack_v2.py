from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


RevisionOperation = Literal[
    "initial_generate",
    "import",
    "text_fix",
    "tts_fix",
    "recording",
    "recording_reset",
    "add_file",
    "remove_file",
    "partial_generate",
    "manual_admin",
]
PackItemKind = Literal["document", "quiz"]
ReRecordReason = Literal["text_changed", "tts_changed", "audio_reset", "manual"]


class ManifestCreatorV2(BaseModel):
    id: str = Field(..., min_length=1)
    displayName: str | None = None


class ChangedUnit(BaseModel):
    fileName: str = Field(..., min_length=1)
    unitId: str | None = None
    fields: list[str] = Field(default_factory=list)
    category: str | None = None


class ReRecordNeededUnit(BaseModel):
    fileName: str = Field(..., min_length=1)
    unitId: str | None = None
    reason: ReRecordReason


class RemovedFileRef(BaseModel):
    logicalId: str = Field(..., min_length=1)
    fileVersionId: str | None = None
    reason: str | None = None


class ManifestChange(BaseModel):
    operation: RevisionOperation
    changedFiles: list[str] = Field(default_factory=list)
    addedFiles: list[str] = Field(default_factory=list)
    removedFiles: list[str] = Field(default_factory=list)
    changedUnits: list[ChangedUnit] = Field(default_factory=list)
    reRecordNeededUnits: list[ReRecordNeededUnit] = Field(default_factory=list)
    removedFileRefs: list[RemovedFileRef] = Field(default_factory=list)
    note: str | None = None


class ManifestItemV2(BaseModel):
    kind: PackItemKind
    name: str = Field(..., min_length=1)
    title: str | None = None
    logicalId: str = Field(..., min_length=1)
    fileVersionId: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    sizeBytes: int | None = Field(default=None, ge=0)
    contentHash: str | None = None


class PackManifestV2(BaseModel):
    id: str = Field(..., min_length=1)
    type: Literal["pack_manifest"] = "pack_manifest"
    schemaVersion: Literal[1] = 1
    contentId: str = Field(..., min_length=1)
    slug: str | None = None
    title: str | None = None
    description: str = ""
    language: str = "ja"
    generationMode: Literal["standard", "language_learning"] = "standard"
    author: str | None = None
    scale: str | None = None
    globalTags: list[str] = Field(default_factory=list)
    creator: ManifestCreatorV2
    revision: int = Field(..., ge=1)
    versionId: str = Field(..., min_length=1)
    sourceVersionId: str | None = None
    buildId: str = Field(..., min_length=1)
    generatedAt: str = Field(..., min_length=1)
    change: ManifestChange
    qualityStatus: Literal["valid", "warning", "blocked"] = "valid"
    publicationStatus: Literal["draft", "published"] = "draft"
    items: list[ManifestItemV2] = Field(default_factory=list)

    @model_validator(mode="after")
    def logical_ids_are_unique(self) -> "PackManifestV2":
        logical_ids = [item.logicalId for item in self.items]
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError("manifest items logicalId values must be unique")
        return self


class PackLatestV2(BaseModel):
    type: Literal["pack_latest"] = "pack_latest"
    schemaVersion: Literal[2] = 2
    creatorId: str = Field(..., min_length=1)
    contentId: str = Field(..., min_length=1)
    storagePrefix: str = Field(..., min_length=1)
    versionId: str = Field(..., min_length=1)
    revision: int = Field(..., ge=1)
    manifestUrl: str = Field(..., min_length=1)
    assetBaseUrl: str = Field(..., min_length=1)
    title: str | None = None
    description: str = ""
    slug: str | None = None
    language: str = "ja"
    generatedAt: str = Field(..., min_length=1)
    change: ManifestChange
    qualityStatus: Literal["valid", "warning", "blocked"] = "valid"
    publicationStatus: Literal["draft", "published"] = "draft"
    items: list[ManifestItemV2] = Field(default_factory=list)


class RevisionTarget(BaseModel):
    creatorId: str = Field(..., min_length=1)
    contentId: str = Field(..., min_length=1)
    versionId: str | None = None
    manifestUrl: str | None = None


class ChangedPackFile(BaseModel):
    name: str = Field(..., min_length=1)
    kind: PackItemKind
    logicalId: str = Field(..., min_length=1)
    previousFileVersionId: str = Field(..., min_length=1)
    content: dict[str, Any]


class AddedPackFile(BaseModel):
    name: str = Field(..., min_length=1)
    kind: PackItemKind
    logicalId: str = Field(..., min_length=1)
    content: dict[str, Any]


class RemovedPackFile(BaseModel):
    logicalId: str = Field(..., min_length=1)
    name: str | None = None
    kind: PackItemKind | None = None
    previousFileVersionId: str | None = None
    reason: str | None = None


class AudioObject(BaseModel):
    audioVersionId: str = Field(..., min_length=1)
    relativePath: str = Field(..., min_length=1)
    data: bytes = b""
    contentType: Literal["audio/mpeg"] = "audio/mpeg"
    contentHash: str | None = None


class RemovedAudioRef(BaseModel):
    fileName: str = Field(..., min_length=1)
    unitId: str | None = None
    field: str = Field(..., min_length=1)
    audioPath: str | None = None


class CommitPackRevisionInput(BaseModel):
    target: RevisionTarget
    operation: RevisionOperation
    slug: str | None = None
    title: str | None = None
    description: str | None = None
    language: str | None = None
    generationMode: Literal["standard", "language_learning"] | None = None
    author: str | None = None
    scale: str | None = None
    globalTags: list[str] | None = None
    creatorDisplayName: str | None = None
    qualityStatus: Literal["valid", "warning", "blocked"] | None = None
    publicationStatus: Literal["draft", "published"] | None = None
    changedFiles: list[ChangedPackFile] = Field(default_factory=list)
    addedFiles: list[AddedPackFile] = Field(default_factory=list)
    removedFiles: list[RemovedPackFile] = Field(default_factory=list)
    changedUnits: list[ChangedUnit] = Field(default_factory=list)
    newAudioObjects: list[AudioObject] = Field(default_factory=list)
    removedAudioRefs: list[RemovedAudioRef] = Field(default_factory=list)
    reRecordNeededUnits: list[ReRecordNeededUnit] = Field(default_factory=list)
    note: str | None = None

    @field_validator("changedFiles", "addedFiles", "removedFiles")
    @classmethod
    def file_lists_must_not_have_duplicate_logical_ids(cls, value: list[Any]) -> list[Any]:
        logical_ids = [item.logicalId for item in value]
        if len(logical_ids) != len(set(logical_ids)):
            raise ValueError("logicalId values must be unique within each commit file list")
        return value


class PackObjectToSave(BaseModel):
    kind: PackItemKind
    name: str = Field(..., min_length=1)
    logicalId: str = Field(..., min_length=1)
    fileVersionId: str = Field(..., min_length=1)
    relativePath: str = Field(..., min_length=1)
    content: dict[str, Any]
    contentHash: str | None = None


class RevisionCommitResult(BaseModel):
    contentId: str
    revision: int
    versionId: str
    sourceVersionId: str | None = None
    manifestUrl: str
    assetBaseUrl: str
    items: list[ManifestItemV2]
    changedFiles: list[str] = Field(default_factory=list)
    addedFiles: list[str] = Field(default_factory=list)
    removedFiles: list[str] = Field(default_factory=list)
    reRecordNeededUnits: list[ReRecordNeededUnit] = Field(default_factory=list)
    manifest: PackManifestV2
    docObjects: list[PackObjectToSave] = Field(default_factory=list)
    quizObjects: list[PackObjectToSave] = Field(default_factory=list)
    audioObjects: list[AudioObject] = Field(default_factory=list)
