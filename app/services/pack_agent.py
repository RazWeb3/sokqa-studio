import logging

from app.schemas.pack_v2 import AddedPackFile, ChangedPackFile, CommitPackRevisionInput, PackManifestV2, RevisionTarget
from app.schemas.request import GeneratePackRequest, PlanPackRequest, ReviseTtsRequest
from app.schemas.common import normalize_tts_reading_mode, source_mode_for_material_mode
from app.schemas.sokqa import CoursePlan, DebugPromptRecordSchema, GeneratePackResponse, GeneratedFile, PlanDocument
from app.config import get_settings
from app.services.document_generator import (
    STRICT_MAX_DOCUMENT_FILES,
    generate_document_pack,
    generate_strict_source_document_pack,
    split_strict_source_sections,
    strict_source_limit_error,
    strict_source_paragraphs,
)
from app.services.exporter import build_generated_files
from app.services.gemini_client import pop_debug_prompts
from app.services.generation_status import pop_generation_events
from app.services.job_store import get_job, save_job, update_job
from app.services.model_resolver import resolve_task_models
from app.services.pack_metadata import build_pack_metadata, resolve_plan_identity
from app.services.planner import create_course_plan
from app.services.quiz_generator import generate_quiz_pack
from app.services.repairer import repair_files
from app.services.revision_commit import build_revision_commit
from app.services.revision_store import persist_revision_commit
from app.services.pack_revision_tools import apply_tts_replacement_rules
from app.services.source_material import normalize_source
from app.services.storage_client import StorageClient
from app.services.storage_status import pop_storage_events
from app.services.tts_optimizer import optimize_generated_files_with_report
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


def _resolve_selected_reading_patterns(plan):
    available_ids = {pattern.id for pattern in plan.proposedReadingPatterns}
    selected_ids = [pattern_id for pattern_id in plan.selectedReadingPatternIds if pattern_id in available_ids]
    if selected_ids == plan.selectedReadingPatternIds:
        return plan
    return plan.model_copy(update={"selectedReadingPatternIds": selected_ids})


def _apply_generation_controls(plan: CoursePlan, request: GeneratePackRequest) -> CoursePlan:
    updates = {}
    for key in ["structurePolicy", "generationUnit", "docCount", "quizCount", "questionCount", "sectionsPerDocument", "materialMode", "customInstructions"]:
        value = getattr(request, key, None)
        if value is not None:
            updates[key] = value
    if updates:
        plan = plan.model_copy(update=updates)
    if plan.materialMode == "strict" and plan.generationUnit != "document":
        plan = plan.model_copy(update={"materialMode": "source_only"})

    doc_count = plan.docCount
    quiz_count = plan.quizCount
    if plan.generationUnit == "document":
        quiz_count = 0 if quiz_count is None else quiz_count
    elif plan.generationUnit == "quiz":
        doc_count = 0 if doc_count is None else doc_count

    documents = plan.documents
    quiz_packs = plan.quizPacks
    if doc_count is not None:
        documents = documents[:doc_count]
    if quiz_count is not None:
        quiz_packs = quiz_packs[:quiz_count]
    if plan.sectionsPerDocument is not None:
        documents = [
            document.model_copy(update={"targetSectionCount": plan.sectionsPerDocument})
            for document in documents
        ]
    if plan.questionCount is not None:
        quiz_packs = [
            quiz_pack.model_copy(update={"questionCount": plan.questionCount})
            for quiz_pack in quiz_packs
        ]
    return plan.model_copy(update={"docCount": doc_count, "quizCount": quiz_count, "documents": documents, "quizPacks": quiz_packs})


def _strict_source_plan_documents(plan: CoursePlan, chunks: list[list[str]]) -> list[PlanDocument]:
    return [
        PlanDocument(
            id=f"doc_{index:02d}",
            title=f"{plan.shortTitle or plan.title} {index}. 資料ファイル {index}",
            goal=f"元資料の段落{start + 1}〜{end}を改変せずに格納する",
            keyPoints=[],
            targetSectionCount=len(chunk),
        )
        for index, (chunk, start, end) in enumerate(_chunks_with_offsets(chunks), start=1)
    ]


def _chunks_with_offsets(chunks: list[list[str]]) -> list[tuple[list[str], int, int]]:
    offset = 0
    indexed = []
    for chunk in chunks:
        start = offset
        offset += len(chunk)
        indexed.append((chunk, start, offset))
    return indexed


def _strict_source_chunks_or_error(source_text: str) -> list[list[str]]:
    paragraphs = strict_source_paragraphs(source_text) or [source_text.strip()]
    chunks = split_strict_source_sections(paragraphs)
    error = strict_source_limit_error(len(chunks))
    if error:
        raise RuntimeError(error)
    return chunks


def _apply_strict_source_layout(plan: CoursePlan, chunks: list[list[str]]) -> CoursePlan:
    return plan.model_copy(
        update={
            "documents": _strict_source_plan_documents(plan, chunks),
            "docCount": None,
            "quizCount": 0 if plan.generationUnit == "document" else plan.quizCount,
            "strictSourceSectionCount": sum(len(chunk) for chunk in chunks),
            "strictSourceFileCount": len(chunks),
            "strictSourceMaxFiles": STRICT_MAX_DOCUMENT_FILES,
            "strictSourceLimitExceeded": False,
        }
    )


def _effective_tts_mode(plan: CoursePlan, requested_mode=None):
    requested_mode = normalize_tts_reading_mode(requested_mode)
    if requested_mode:
        return requested_mode
    if not plan.enableTtsOptimize:
        return "none"
    return normalize_tts_reading_mode(plan.ttsReadingMode) or get_settings().tts_reading_mode


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
    plan = _apply_generation_controls(plan, request)
    source_mode = source_mode_for_material_mode(plan.materialMode, source_mode) if source_text else None
    plan.sourceText = source_text
    plan.sourceMode = source_mode
    if request.ttsLanguageSettings is not None:
        plan.ttsLanguageSettings = request.ttsLanguageSettings
    plan = _resolve_selected_reading_patterns(plan)
    tts_mode = _effective_tts_mode(plan, request.ttsReadingMode)
    if tts_mode != "llm" and plan.selectedReadingPatternIds:
        plan = plan.model_copy(update={"selectedReadingPatternIds": []})
    models = resolve_task_models(plan, request)
    logs.append(f"Model planner: {models.planner}")
    logs.append(f"Model document: {models.document}")
    logs.append(f"Model quiz: {models.quiz}")

    if plan.materialMode == "strict" and source_text:
        strict_chunks = _strict_source_chunks_or_error(source_text)
        plan = _apply_strict_source_layout(plan, strict_chunks)
        logs.append(
            "Generating Documents from source material "
            f"(strict: {len(strict_chunks)} files / {sum(len(chunk) for chunk in strict_chunks)} sections)"
        )
        document_packs = [
            generate_strict_source_document_pack(plan, document, sections=sections)
            for document, sections in zip(plan.documents, strict_chunks)
        ]
    else:
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

    if tts_mode != "none":
        logs.append(f"Optimizing TTS ({tts_mode})")
        files, tts_report = optimize_generated_files_with_report(files, plan.ttsRules, tts_mode, plan.ttsLanguageSettings)
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
    prompts = _collect_prompt_records()
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
        prompts=prompts,
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

    content_files = [
        file.model_copy(deep=True)
        for file in existing.files
        if file.kind in {"document", "quiz"}
    ]
    revised_files, tts_revisions = apply_tts_replacement_rules(content_files, request.ttsRules)

    logs = [*existing.logs, "Revising TTS", "Applying TTS replacement rules"]
    if not isinstance(existing.manifest, PackManifestV2):
        raise ValueError("revise_tts requires a revision manifest")

    if not revised_files:
        validation = validate_files(existing.files, existing.manifest)
        append_validation_logs(logs, validation)
        revised = existing.model_copy(
            update={
                "plan": plan,
                "validation": validation,
                "ttsRevisions": [],
                "logs": [*logs, "No TTS replacement changes"],
            }
        )
        update_job(revised)
        return revised

    logs.append("Persisting TTS revision" if request.persist else "Building TTS revision")
    commit_result = _persist_changed_revision(plan, metadata, revised_files, existing.manifest, request.persist)
    if request.persist:
        logs.extend(event.message for event in pop_storage_events())
    manifest = commit_result.manifest
    revised_files = _files_from_revision_result(commit_result)

    validation = validate_files(revised_files, manifest)
    append_validation_logs(logs, validation)
    new_prompts = _collect_prompt_records()
    existing_prompts = list(existing.prompts or [])
    revised = GeneratePackResponse(
        status="completed",
        jobId=existing.jobId,
        plan=plan,
        files=revised_files,
        manifest=manifest,
        validation=validation,
        ttsReport=None,
        ttsRevisions=tts_revisions,
        logs=logs,
        prompts=existing_prompts + new_prompts,
    )
    update_job(revised)
    return revised


def append_validation_logs(logs: list[str], validation) -> None:
    if validation.valid:
        return
    for error in validation.errors:
        logs.append(f"validation error: {error.file} {error.path}: {error.message}")


def _collect_prompt_records() -> list[DebugPromptRecordSchema]:
    return [
        DebugPromptRecordSchema(
            prompt_type=record.prompt_type,
            target=record.target,
            model=record.model,
            prompt=record.prompt,
            generated_at=record.generated_at,
            characters=record.characters,
            doc_title=record.doc_title,
            quiz_title=record.quiz_title,
            file_name=record.file_name,
            phase=record.phase,
            run_index=record.run_index,
        )
        for record in pop_debug_prompts()
    ]


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
