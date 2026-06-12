from fastapi import APIRouter, HTTPException

from app.schemas.quality import QualityCheckRequest, QualityCheckResponse
from app.services.quality_checker import QualityCheckError, check_pack_quality


router = APIRouter(tags=["quality"])


@router.post("/quality-check", response_model=QualityCheckResponse)
def quality_check(request: QualityCheckRequest) -> QualityCheckResponse:
    try:
        return check_pack_quality(request.target, request.maxIssues)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except QualityCheckError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
