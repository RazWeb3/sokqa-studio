from __future__ import annotations

from pathlib import PurePath

from app.schemas.pack_v2 import AddedPackFile, CommitPackRevisionInput, PackManifestV2, RevisionTarget
from app.schemas.request import ImportPackRequest, ImportPackResponse
from app.schemas.sokqa import (
    CoursePlan,
    GeneratedFile,
    SokqaDocumentPack,
    SokqaQuizPack,
)
from app.services.pack_metadata import build_pack_metadata
from app.services.revision_store import persist_revision_commit
from app.services.storage_client import StorageClient
from app.services.validator import validate_files


def import_pack_files(request: ImportPackRequest) -> ImportPackResponse:
    imported_manifest: PackManifestV2 | None = None
    files: list[GeneratedFile] = []
    logs: list[str] = []

    for input_file in request.files:
        name = _safe_json_name(input_file.name)
        content_type = input_file.content.get("type")
        if content_type == "pack_manifest":
            imported_manifest = PackManifestV2.model_validate(input_file.content)
            logs.append(f"manifest loaded: {name}")
            continue
        if content_type == "document":
            pack = SokqaDocumentPack.model_validate(input_file.content)
            _clear_document_audio_urls(pack)
            files.append(GeneratedFile(name=name, kind="document", content=pack.model_dump(exclude_none=True)))
            logs.append(f"document loaded: {name}")
            continue
        if content_type == "quiz":
            pack = SokqaQuizPack.model_validate(input_file.content)
            _clear_quiz_audio_urls(pack)
            files.append(GeneratedFile(name=name, kind="quiz", content=pack.model_dump(exclude_none=True)))
            logs.append(f"quiz loaded: {name}")
            continue
        raise ValueError(f"unsupported JSON type in {name}: {content_type or 'missing'}")

    if not files:
        raise ValueError("import requires at least one document or quiz JSON")

    files = _deduplicate_file_names(files)
    title = _resolve_title(request, imported_manifest, files)
    plan_id = request.contentId or (imported_manifest.contentId if imported_manifest else None) or str(files[0].content.get("id", "imported-pack"))
    plan = CoursePlan(
        id=plan_id,
        creatorId=request.creatorId or (imported_manifest.creator.id if imported_manifest and imported_manifest.creator else None),
        creatorDisplayName=request.creatorDisplayName
        if request.creatorDisplayName is not None
        else (imported_manifest.creator.displayName if imported_manifest and imported_manifest.creator else None),
        contentId=request.contentId or (imported_manifest.contentId if imported_manifest else None),
        slug=request.slug or (imported_manifest.slug if imported_manifest else None),
        title=title,
        description=(imported_manifest.description if imported_manifest else "") or "",
        language=(imported_manifest.language if imported_manifest else files[0].content.get("language")) or "ja",
        targetUser="imported",
        difficulty="beginner",
        scale=getattr(imported_manifest, "scale", None) if imported_manifest else None,
        author=(imported_manifest.author if imported_manifest else files[0].content.get("author")) or "Sokqa Team",
        documents=[],
        quizPacks=[],
    )
    metadata = build_pack_metadata(
        plan,
        creator_id=request.creatorId,
        creator_display_name=request.creatorDisplayName,
        content_id=request.contentId,
        slug=request.slug,
    )
    commit_result = persist_revision_commit(
        StorageClient(),
        None,
        CommitPackRevisionInput(
            target=RevisionTarget(creatorId=metadata.creator_id, contentId=metadata.content_id),
            operation="import",
            slug=metadata.slug,
            title=title,
            description=plan.description,
            language=plan.language,
            author=plan.author,
            scale=plan.scale,
            globalTags=getattr(imported_manifest, "globalTags", []) if imported_manifest else [],
            creatorDisplayName=metadata.creator_display_name,
            addedFiles=[_added_file(file) for file in files],
        ),
    )
    manifest = commit_result.manifest
    saved_files = _files_from_revision_result(commit_result)
    validation = validate_files(saved_files, manifest)
    return ImportPackResponse(files=saved_files, manifest=manifest, validation=validation, logs=logs)


def _added_file(file: GeneratedFile) -> AddedPackFile:
    return AddedPackFile(
        name=file.name,
        kind=file.kind,
        logicalId=_logical_id_for_file(file.name),
        content=file.content,
    )


def _logical_id_for_file(name: str) -> str:
    return name[:-5] if name.lower().endswith(".json") else name


def _files_from_revision_result(result) -> list[GeneratedFile]:
    objects_by_name = {obj.name: obj for obj in [*result.docObjects, *result.quizObjects]}
    files = [
        GeneratedFile(
            name=item.name,
            kind=item.kind,
            content=objects_by_name[item.name].content,
            url=item.url,
        )
        for item in result.items
        if item.name in objects_by_name
    ]
    files.append(
        GeneratedFile(
            name="manifest.json",
            kind="manifest",
            content=result.manifest.model_dump(mode="json", exclude_none=True),
            url=result.manifestUrl,
        )
    )
    return files


def _safe_json_name(name: str) -> str:
    base = PurePath(name.replace("\\", "/")).name.strip() or "pack.json"
    safe = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in base)
    if not safe.lower().endswith(".json"):
        safe = f"{safe}.json"
    return safe or "pack.json"


def _deduplicate_file_names(files: list[GeneratedFile]) -> list[GeneratedFile]:
    seen: set[str] = set()
    result: list[GeneratedFile] = []
    for file in files:
        name = file.name
        if name in seen:
            stem = name[:-5] if name.lower().endswith(".json") else name
            suffix = 2
            while f"{stem}_{suffix}.json" in seen:
                suffix += 1
            name = f"{stem}_{suffix}.json"
        seen.add(name)
        result.append(file.model_copy(update={"name": name}))
    return result


def _resolve_title(request: ImportPackRequest, manifest: PackManifestV2 | None, files: list[GeneratedFile]) -> str:
    if request.title:
        return request.title
    if manifest and manifest.title:
        return manifest.title
    return str(files[0].content.get("title") or files[0].content.get("id") or "Imported Sokqa Pack")


def _clear_document_audio_urls(pack: SokqaDocumentPack) -> None:
    pack.assetBaseUrl = None
    for item in pack.documents:
        if item.tts:
            item.tts.audioPath = None
            item.tts.audioUrl = None


def _clear_quiz_audio_urls(pack: SokqaQuizPack) -> None:
    pack.assetBaseUrl = None
    for question in pack.questions:
        if question.tts:
            question.tts.questionAudioPath = None
            question.tts.questionAudioUrl = None
            question.tts.choiceAudioPaths = None
            question.tts.choiceAudioUrls = None
            question.tts.explanationAudioPath = None
            question.tts.explanationAudioUrl = None
