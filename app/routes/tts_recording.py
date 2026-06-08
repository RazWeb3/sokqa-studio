from fastapi import APIRouter, HTTPException

from app.schemas.request import EstimateTtsRecordingRequest, ResetTtsRecordingRequest, RunTtsRecordingRequest
from app.services.tts_recording_api import estimate_recording, reset_recording, run_recording


router = APIRouter(prefix="/tts", tags=["tts-recording"])


@router.post("/recording-estimate")
def estimate_tts_recording(request: EstimateTtsRecordingRequest) -> dict:
    try:
        return estimate_recording(request.target, request.unitIds, request.textSource)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/record")
def run_tts_recording(request: RunTtsRecordingRequest) -> dict:
    try:
        return run_recording(request.target, request.unitIds, request.textSource, request.forceRerecord)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/recording-reset")
@router.post("/recording_reset", include_in_schema=False)
@router.post("/reset-recording", include_in_schema=False)
def reset_tts_recording(request: ResetTtsRecordingRequest) -> dict:
    try:
        return reset_recording(request.target, request.unitIds, request.textSource)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
