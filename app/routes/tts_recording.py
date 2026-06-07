from fastapi import APIRouter, HTTPException

from app.schemas.request import EstimateTtsRecordingRequest, RunTtsRecordingRequest
from app.services.tts_recording_api import estimate_recording, run_recording


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
        return run_recording(request.target, request.unitIds, request.textSource)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
