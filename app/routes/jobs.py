from fastapi import APIRouter, HTTPException

from app.schemas.sokqa import GeneratePackResponse, PackManifest
from app.services.job_store import get_job


router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", response_model=GeneratePackResponse)
def read_job(job_id: str) -> GeneratePackResponse:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job


@router.get("/jobs/{job_id}/manifest", response_model=PackManifest)
def read_job_manifest(job_id: str) -> PackManifest:
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return job.manifest
