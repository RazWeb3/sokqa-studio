import logging

from app.schemas.pack_v2 import AddedPackFile, ChangedPackFile, CommitPackRevisionInput, PackManifestV2, RevisionTarget
from app.schemas.request import GeneratePackRequest, PlanPackRequest, ReviseTtsRequest
from app.schemas.sokqa import GeneratePackResponse, GeneratedFile
from app.config import get_settings
from app.services.document_generator import generate_document_pack
from app.services.exporter import build_generated_files
from app.services.generation_status import pop_generation_events
from app.services.job_store import get_job, save_job, update_job
from app.services.model_resolver import resolve_task_models
from app.services.pack_metadata import build_pack_metadata, resolve_plan_identity
from app.services.planner import create_course_plan
from app.services.quiz_generator import generate_quiz_pack
from app.services.repairer import repair_files
from app.services.revision_commit import build_revision_commit
from app.services.revision_store import persist_revision_commit
from app.services.source_material import normalize_source
from app.services.storage_client import StorageClient
from app.services.storage_status import pop_storage_events
from app.services.tts_optimizer import optimize_generated_files, optimize_generated_files_with_report
from app.services.validator import validate_files
from app.services.versioning import bump_patch
from app.utils.ids import new_job_id

logger = logging.getLogger(__name__)


def _logical_id_for_file(file: GeneratedFile) -> str:
    name = file.name[:-5] if file.name.lower().endswith(".json") else file.name
    return name


def _added_file(file: GeneratedFile) -> AddedPackFile:
    return AddedPackFile(
        name=file.name,
        kind=file.kind,
        logicalId=_logical_id_for_file(file),
        content=file.content,
    )


def _changed_file(file: GeneratedFile, current_manifest: PackManifestV2) -> ChangedPackFile:
    logical_id = _logical_id_for_file(file)
    item = next((item for item in current_manifest.items if item.logicalId == logical_id), None)
    if item is None:
        raise ValueError(f"file is not present in current manifest: {file.name}")
    return ChangedPackFile(
        name=file.name,
        kind=file.kind,
        logicalId=logical_id,
        previousFileVersionId=item.fileVersionId,
        content=file.content,
    )


def _files_from_revision_result(result) -> list[GeneratedFile]:
    files: list[GeneratedFile] = []
    objects_by_name = {obj.name: obj for obj in [*result.docObjects, *result.quizObjects]}
    for item in result.items:
        obj = objects_by_name.get(item.name)
        content = obj.content if obj is not None else {}
        files.append(GeneratedFile(name=item.name, kind=item.kind, content=content, url=item.url))
    files.append(
        GeneratedFile(
            name="manifest.json",
            kind="manifest",
            content=result.manifest.model_dump(mode="json", exclude_none=True),
            url=result.manifestUrl,
        )
    )
    return files


def _initial_commit_request(plan, metadata, files: list[GeneratedFile], operation: str) -> CommitPackRevisionInput:
    return CommitPackRevisionInput(
        target=RevisionTarget(creatorId=metadata.creator_id, contentId=metadata.content_id),
        operation=operation,
        slug=metadata.slug,
        title=plan.title,
        description=plan.description,
        language=plan.language,
        author=plan.author,
        scale=plan.scale,
        globalTags=getattr(plan, "globalTags", []),
        creatorDisplayName=metadata.creator_display_name,
        addedFiles=[_added_file(file) for file in files],
    )


def _persist_initial_revision(plan, metadata, files: list[GeneratedFile], operation: str, persist: bool):
    storage = StorageClient()
    request = _initial_commit_request(plan, metadata, files, operation)
    if persist:
        return persist_revision_commit(storage, None, request)
    return build_revision_commit(None, request)


def _persist_changed_revision(
    plan,
    metadata,
    files: list[GeneratedFile],
    current_manifest: PackManifestV2,
    persist: bool,
):
    request = CommitPackRevisionInput(
        target=RevisionTarget(
            creatorId=metadata.creator_id,
            contentId=metadata.content_id,
            versionId=current_manifest.versionId,
        ),
        operation="tts_fix",
        changedFiles=[_changed_file(file, current_manifest) for file in files],
    )
    if persist:
        return persist_revision_commit(StorageClient(), current_manifest, request)
    return build_revision_commit(current_manifest, request)


def _document_packs_for_quiz(plan, quiz_pack, document_packs):
    if not quiz_pack.sourceDocumentIds:
        return document_packs
    packs_by_document_id = {
        document.id: document_pack
        for document, document_pack in zip(plan.documents, document_packs)
    }
    selected = [
        packs_by_document_id[document_id]
        for document_id in quiz_pack.sourceDocumentIds
        if document_id in packs_by_document_id
    ]
    return selected or document_packs


def plan_pack(request: PlanPackRequest):
    models = resolve_task_models(request=request)
    plan = create_course_plan(request, model=models.planner)
    plan = resolve_plan_identity(plan)
    plan.model = request.model
    plan.docModel = request.docModel or request.model
    plan.quizModel = request.quizModel or request.model
    plan.plannerModel = request.plannerModel or request.model
    return plan


def generate_pack(request: GeneratePackRequest) -> GeneratePackResponse:
    logs: list[str] = ["Planning"]
    plan = resolve_plan_identity(
        request.plan.model_copy(
            update={
                key: value
                for key, value in {
                    "creatorId": request.creatorId,
                    "creatorDisplayName": request.creatorDisplayName,
                    "contentId": request.contentId,
                    "slug": request.slug,
                }.items()
                if value is not None
            }
        )
    )
    metadata = build_pack_metadata(
        plan,
        creator_id=request.creatorId,
        creator_display_name=request.creatorDisplayName,
        content_id=request.contentId,
        slug=request.slug,
    )
    source_text, source_mode = normalize_source(
        request.sourceText if request.sourceText is not None else plan.sourceText,
        request.sourceMode if request.sourceMode is not None else plan.sourceMode,
    )
    plan.sourceText = source_text
    plan.sourceMode = source_mode
    models = resolve_task_models(plan, request)
    logs.append(f"Model planner: {models.planner}")
    logs.append(f"Model document: {models.document}")
    logs.append(f"Model quiz: {models.quiz}")

    logs.append("Generating Documents")
    document_packs = [generate_document_pack(plan, document, model=models.document) for document in plan.documents]
    logs.extend(event.message for event in pop_generation_events())

    logs.append("Generating Quizzes from Documents")
    quiz_packs = [
        generate_quiz_pack(
            plan,
            quiz_pack,
            _document_packs_for_quiz(plan, quiz_pack, document_packs),
            model=models.quiz,
        )
        for quiz_pack in plan.quizPacks
    ]
    logs.extend(event.message for event in pop_generation_events())

    files = build_generated_files(document_packs, quiz_packs)

    logs.append("Validating")
    validation = validate_files(files)
    append_validation_logs(logs, validation)

    if _needs_generation_repair(validation):
        logs.append("Repairing")
        files = repair_files(files)
        validation = validate_files(files)
        append_validation_logs(logs, validation)
        _log_remaining_repair_warnings(validation)

    if plan.enableTtsOptimize:
        tts_mode = request.ttsReadingMode or plan.ttsReadingMode
        logs.append(f"Optimizing TTS ({tts_mode or get_settings().tts_reading_mode})")
        files, tts_report = optimize_generated_files_with_report(files, plan.ttsRules, tts_mode)
        validation = validate_files(files)
        append_validation_logs(logs, validation)
    else:
        tts_report = None
        logs.append("Skipping TTS optimization")

    logs.append("Persisting generated files" if request.persist else "Building Manifest")
    commit_result = _persist_initial_revision(plan, metadata, files, "initial_generate", request.persist)
    if request.persist:
        logs.extend(event.message for event in pop_storage_events())
    manifest = commit_result.manifest
    files = _files_from_revision_result(commit_result)

    validation = validate_files(files, manifest)
    append_validation_logs(logs, validation)
    job_id = new_job_id()
    response = GeneratePackResponse(
        status="completed",
        jobId=job_id,
        plan=plan,
        files=files,
        manifest=manifest,
        validation=validation,
        ttsReport=tts_report,
        logs=logs,
    )
    save_job(response)
    return response


def revise_tts(request: ReviseTtsRequest) -> GeneratePackResponse:
    existing = get_job(request.jobId)
    if not existing:
        raise ValueError("job not found")

    plan = resolve_plan_identity(existing.plan.model_copy(deep=True))
    metadata = build_pack_metadata(plan)
    plan.version = bump_patch(plan.version)
    plan.ttsRules.extend(request.ttsRules)

    content_files = [
        file.model_copy(deep=True)
        for file in existing.files
        if file.kind in {"document", "quiz"}
    ]
    optimized_files = optimize_generated_files(content_files, plan.ttsRules, plan.ttsReadingMode)

    logs = [*existing.logs, "Revising TTS", "Optimizing TTS"]
    if not isinstance(existing.manifest, PackManifestV2):
        raise ValueError("revise_tts requires a revision manifest")

    logs.append("Persisting TTS revision" if request.persist else "Building TTS revision")
    commit_result = _persist_changed_revision(plan, metadata, optimized_files, existing.manifest, request.persist)
    if request.persist:
        logs.extend(event.message for event in pop_storage_events())
    manifest = commit_result.manifest
    optimized_files = _files_from_revision_result(commit_result)

    validation = validate_files(optimized_files, manifest)
    append_validation_logs(logs, validation)
    revised = GeneratePackResponse(
        status="completed",
        jobId=existing.jobId,
        plan=plan,
        files=optimized_files,
        manifest=manifest,
        validation=validation,
        ttsReport=None,
        logs=logs,
    )
    update_job(revised)
    return revised


def append_validation_logs(logs: list[str], validation) -> None:
    if validation.valid:
        return
    for error in validation.errors:
        logs.append(f"validation error: {error.file} {error.path}: {error.message}")


def _needs_generation_repair(validation) -> bool:
    return (not validation.valid) or any(_is_repairable_generation_warning(error) for error in validation.errors)


def _is_repairable_generation_warning(error) -> bool:
    return error.severity == "warning" and "citation-style wording" in error.message


def _log_remaining_repair_warnings(validation) -> None:
    for error in validation.errors:
        if _is_repairable_generation_warning(error):
            logger.warning(
                "generation repair warning remains after repair file=%s path=%s message=%s",
                error.file,
                error.path,
                error.message,
            )
