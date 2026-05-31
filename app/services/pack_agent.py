from app.schemas.request import GeneratePackRequest, PlanPackRequest, ReviseTtsRequest
from app.schemas.sokqa import GeneratePackResponse, GeneratedFile
from app.config import get_settings
from app.services.document_generator import generate_document_pack
from app.services.exporter import build_generated_files, build_manifest
from app.services.job_store import get_job, save_job, update_job
from app.services.planner import create_course_plan
from app.services.quiz_generator import generate_quiz_pack
from app.services.repairer import repair_files
from app.services.storage_client import StorageClient
from app.services.tts_optimizer import optimize_generated_files
from app.services.validator import validate_files
from app.services.versioning import bump_patch
from app.utils.ids import new_job_id


def plan_pack(request: PlanPackRequest):
    return create_course_plan(request)


def generate_pack(request: GeneratePackRequest) -> GeneratePackResponse:
    logs: list[str] = ["Planning"]
    plan = request.plan

    logs.append("Generating Documents")
    document_packs = [generate_document_pack(plan, document) for document in plan.documents]

    logs.append("Generating Quizzes from Documents")
    quiz_packs = [generate_quiz_pack(plan, quiz_pack, document_packs) for quiz_pack in plan.quizPacks]

    files = build_generated_files(document_packs, quiz_packs)

    logs.append("Validating")
    validation = validate_files(files)

    if not validation.valid:
        logs.append("Repairing")
        files = repair_files(files)
        validation = validate_files(files)

    logs.append("Optimizing TTS")
    files = optimize_generated_files(files, plan.ttsRules)
    validation = validate_files(files)

    if request.persist:
        logs.append("Uploading to Cloud Storage")
        files = StorageClient().save_files(plan.id, files)
    else:
        base_url = get_settings().public_base_url.rstrip("/")
        for file in files:
            file.url = f"{base_url}/{plan.id}/{file.name}"

    logs.append("Exporting Manifest")
    manifest = build_manifest(plan, files)
    manifest_file = GeneratedFile(
        name="pack_manifest.json",
        kind="manifest",
        content=manifest.model_dump(exclude_none=True),
    )
    if request.persist:
        manifest_file = StorageClient().save_files(plan.id, [manifest_file])[0]
        manifest.items = [
            item.model_copy(update={"url": file.url or item.url})
            for item, file in zip(manifest.items, [f for f in files if f.kind in {"document", "quiz"}])
        ]
        manifest_file.content = manifest.model_dump(exclude_none=True)
    files.append(manifest_file)

    validation = validate_files(files, manifest)
    job_id = new_job_id()
    response = GeneratePackResponse(
        status="completed",
        jobId=job_id,
        plan=plan,
        files=files,
        manifest=manifest,
        validation=validation,
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
    optimized_files = optimize_generated_files(content_files, plan.ttsRules)

    logs = [*existing.logs, "Revising TTS", "Optimizing TTS"]
    if request.persist:
        logs.append("Uploading to Cloud Storage")
        optimized_files = StorageClient().save_files(plan.id, optimized_files)
    else:
        base_url = get_settings().public_base_url.rstrip("/")
        for file in optimized_files:
            file.url = f"{base_url}/{plan.id}/{file.name}"

    logs.append("Exporting Manifest")
    manifest = build_manifest(plan, optimized_files)
    manifest_file = GeneratedFile(
        name="pack_manifest.json",
        kind="manifest",
        content=manifest.model_dump(exclude_none=True),
    )
    if request.persist:
        manifest_file = StorageClient().save_files(plan.id, [manifest_file])[0]
    optimized_files.append(manifest_file)

    validation = validate_files(optimized_files, manifest)
    revised = GeneratePackResponse(
        status="completed",
        jobId=existing.jobId,
        plan=plan,
        files=optimized_files,
        manifest=manifest,
        validation=validation,
        logs=logs,
    )
    update_job(revised)
    return revised
