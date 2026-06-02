from app.schemas.request import GeneratePackRequest, PlanPackRequest, ReviseTtsRequest
from app.schemas.sokqa import GeneratePackResponse, GeneratedFile
from app.config import get_settings
from app.services.document_generator import generate_document_pack
from app.services.exporter import build_generated_files, build_manifest
from app.services.generation_status import pop_generation_events
from app.services.job_store import get_job, save_job, update_job
from app.services.model_resolver import resolve_task_models
from app.services.planner import create_course_plan
from app.services.quiz_generator import generate_quiz_pack
from app.services.repairer import repair_files
from app.services.source_material import normalize_source
from app.services.storage_client import StorageClient
from app.services.storage_status import pop_storage_events
from app.services.tts_optimizer import optimize_generated_files, optimize_generated_files_with_report
from app.services.validator import validate_files
from app.services.versioning import bump_patch
from app.utils.ids import new_job_id


def plan_pack(request: PlanPackRequest):
    models = resolve_task_models(request=request)
    plan = create_course_plan(request, model=models.planner)
    plan.model = request.model
    plan.docModel = request.docModel or request.model
    plan.quizModel = request.quizModel or request.model
    plan.plannerModel = request.plannerModel or request.model
    return plan


def generate_pack(request: GeneratePackRequest) -> GeneratePackResponse:
    logs: list[str] = ["Planning"]
    plan = request.plan
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
    quiz_packs = [generate_quiz_pack(plan, quiz_pack, document_packs, model=models.quiz) for quiz_pack in plan.quizPacks]
    logs.extend(event.message for event in pop_generation_events())

    files = build_generated_files(document_packs, quiz_packs)

    logs.append("Validating")
    validation = validate_files(files)
    append_validation_logs(logs, validation)

    if not validation.valid:
        logs.append("Repairing")
        files = repair_files(files)
        validation = validate_files(files)
        append_validation_logs(logs, validation)

    if plan.enableTtsOptimize:
        tts_mode = request.ttsReadingMode or plan.ttsReadingMode
        logs.append(f"Optimizing TTS ({tts_mode or get_settings().tts_reading_mode})")
        files, tts_report = optimize_generated_files_with_report(files, plan.ttsRules, tts_mode)
        validation = validate_files(files)
        append_validation_logs(logs, validation)
    else:
        tts_report = None
        logs.append("Skipping TTS optimization")

    if request.persist:
        logs.append("Persisting generated files")
        files = StorageClient().save_files(plan.id, files)
        logs.extend(event.message for event in pop_storage_events())
    else:
        base_url = get_settings().public_base_url.rstrip("/")
        for file in files:
            file.url = f"{base_url}/{plan.id}/{file.name}"

    logs.append("Exporting Manifest")
    manifest = build_manifest(plan, files)
    manifest_file = GeneratedFile(
        name="manifest.json",
        kind="manifest",
        content=manifest.model_dump(exclude_none=True),
    )
    if request.persist:
        manifest_file = StorageClient().save_files(plan.id, [manifest_file])[0]
        logs.extend(event.message for event in pop_storage_events())
        manifest.items = [
            item.model_copy(update={"url": file.url or item.url})
            for item, file in zip(manifest.items, [f for f in files if f.kind in {"document", "quiz"}])
        ]
        manifest_file.content = manifest.model_dump(exclude_none=True)
    files.append(manifest_file)

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

    plan = existing.plan.model_copy(deep=True)
    plan.version = bump_patch(plan.version)
    plan.ttsRules.extend(request.ttsRules)

    content_files = [
        file.model_copy(deep=True)
        for file in existing.files
        if file.kind in {"document", "quiz"}
    ]
    optimized_files = optimize_generated_files(content_files, plan.ttsRules, plan.ttsReadingMode)

    logs = [*existing.logs, "Revising TTS", "Optimizing TTS"]
    if request.persist:
        logs.append("Uploading to Cloud Storage")
        optimized_files = StorageClient().save_files(plan.id, optimized_files)
        logs.extend(event.message for event in pop_storage_events())
    else:
        base_url = get_settings().public_base_url.rstrip("/")
        for file in optimized_files:
            file.url = f"{base_url}/{plan.id}/{file.name}"

    logs.append("Exporting Manifest")
    manifest = build_manifest(plan, optimized_files)
    manifest.version = plan.version
    manifest_file = GeneratedFile(
        name="manifest.json",
        kind="manifest",
        content=manifest.model_dump(exclude_none=True),
    )
    if request.persist:
        manifest_file = StorageClient().save_files(plan.id, [manifest_file])[0]
        logs.extend(event.message for event in pop_storage_events())
    optimized_files.append(manifest_file)

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
