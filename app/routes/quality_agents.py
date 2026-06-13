from fastapi import APIRouter, HTTPException

from app.schemas.quality import QualityCheckRequest, QualityCheckResponse
from app.schemas.quality_fix import (
    QualityFixApplyRequest,
    QualityFixApplyResponse,
    QualityFixRequest,
    QualityFixResponse,
    QualityFixSaveRequest,
    QualityFixSaveResponse,
)
from app.services.quality_checker import QualityCheckError, check_text_quality, check_tts_quality
from app.services.quality_fixer import (
    QualityFixError,
    apply_approved_fixes,
    generate_text_fix,
    generate_tts_fix,
    generate_tts_fix_with_llm,
    save_quality_fix_version,
)


router = APIRouter(prefix="/quality", tags=["quality-agents"])


@router.post("/text-check", response_model=QualityCheckResponse)
def text_check(request: QualityCheckRequest) -> QualityCheckResponse:
    try:
        return check_text_quality(request.target, request.maxIssues)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QualityCheckError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/tts-check", response_model=QualityCheckResponse)
def tts_check(request: QualityCheckRequest) -> QualityCheckResponse:
    try:
        return check_tts_quality(request.target, request.maxIssues)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QualityCheckError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/text-fix", response_model=QualityFixResponse)
def text_fix(request: QualityFixRequest) -> QualityFixResponse:
    try:
        return generate_text_fix(request.target, request.issues, request.maxFixes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QualityFixError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/text-fix/apply", response_model=QualityFixApplyResponse)
def text_fix_apply(request: QualityFixApplyRequest) -> QualityFixApplyResponse:
    try:
        return apply_approved_fixes(
            request.updatedJson,
            request.pendingFixes,
            request.approvedIds,
            reset_tts_on_text_change=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tts-fix", response_model=QualityFixResponse)
def tts_fix(request: QualityFixRequest) -> QualityFixResponse:
    try:
        return generate_tts_fix(request.target, request.issues, request.maxFixes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QualityFixError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/tts-fix/llm", response_model=QualityFixResponse)
def tts_fix_llm(request: QualityFixRequest) -> QualityFixResponse:
    try:
        return generate_tts_fix_with_llm(request.target, request.issues, request.maxFixes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QualityFixError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/save-version", response_model=QualityFixSaveResponse)
def save_version(request: QualityFixSaveRequest) -> QualityFixSaveResponse:
    try:
        return save_quality_fix_version(request.target, request.files, request.appliedFixes)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
