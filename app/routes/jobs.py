from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.schemas.pack_v2 import PackManifestV2
from app.schemas.sokqa import DebugPromptRecordSchema, GeneratePackResponse
from app.services.job_store import get_job


router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=GeneratePackResponse)
def read_job(job_id: str) -> GeneratePackResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/jobs/{job_id}/manifest", response_model=PackManifestV2)
def read_job_manifest(job_id: str) -> PackManifestV2:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job.manifest


def _prompt_filename(record: DebugPromptRecordSchema) -> str:
    slug = record.target.replace("/", "_") if record.target else "unknown"
    phase = record.phase or "default"
    run_index = record.run_index or 0
    pack_segment = _safe_filename_token(record.file_name) if record.file_name else ""
    if pack_segment:
        return f"{record.prompt_type}_{pack_segment}_{slug}_{phase}_{run_index}_prompt.txt"
    return f"{record.prompt_type}_{slug}_{phase}_{run_index}_prompt.txt"


def _safe_filename_token(value: str) -> str:
    import re
    token = re.sub(r'[\\/:\"*?<>|]+', "_", value.strip())
    return token.strip("_") or "unknown"


def _prompt_header(record: DebugPromptRecordSchema) -> str:
    lines = [
        "========================================",
        f"Prompt Type : {record.prompt_type}",
        f"Target      : {record.target}",
        f"Model       : {record.model}",
        f"Generated   : {record.generated_at}",
        f"Characters  : {record.characters:,}",
        f"Phase       : {record.phase or 'default'}",
        f"Run Index   : {record.run_index or 0}",
    ]
    if record.doc_title:
        lines.append(f"Doc Title   : {record.doc_title}")
    if record.quiz_title:
        lines.append(f"Quiz Title  : {record.quiz_title}")
    lines.extend(["========================================", ""])
    return "\n".join(lines)


@router.get("/jobs/{job_id}/prompts/download")
def download_job_prompts(job_id: str) -> StreamingResponse:
    """ZIPでプロンプト全文をダウンロードする。DEBUG_PROMPTS_ENABLED=true でのみ有効。"""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if not job.prompts:
        raise HTTPException(status_code=404, detail="no prompts available (DEBUG_PROMPTS_ENABLED may be false)")

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as zf:
        for record in job.prompts:
            header = _prompt_header(record)
            body = record.prompt
            filename = _prompt_filename(record)
            zf.writestr(filename, header + "\n" + body + "\n")
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{job_id}_prompts.zip"'},
    )
