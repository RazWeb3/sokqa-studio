from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.schemas.request import (
    OptimizeTtsRequest,
    RepairPackRequest,
    ReviseTtsRequest,
    SaveTtsRulesRequest,
    TtsRulesConfigResponse,
    ValidatePackRequest,
)
from app.schemas.sokqa import GeneratePackResponse, GeneratedFile, ValidationResult
from app.services.pack_agent import revise_tts
from app.services.gemini_client import GeminiClient
from app.services.repairer import repair_files
from app.services.tts_optimizer import optimize_generated_files
from app.services.tts_rules import load_system_tts_rules, save_system_tts_rules
from app.services.validator import validate_files


router = APIRouter(prefix="/debug", tags=["debug"])


@router.post("/validate-pack", response_model=ValidationResult)
def validate_pack(request: ValidatePackRequest) -> ValidationResult:
    return validate_files(request.files, request.manifest)


@router.post("/repair-pack", response_model=list[GeneratedFile])
def repair_pack(request: RepairPackRequest) -> list[GeneratedFile]:
    return repair_files(request.files)


@router.post("/optimize-tts", response_model=list[GeneratedFile])
def optimize_tts(request: OptimizeTtsRequest) -> list[GeneratedFile]:
    return optimize_generated_files(request.files, request.ttsRules)


@router.post("/revise-tts", response_model=GeneratePackResponse)
def revise_job_tts(request: ReviseTtsRequest) -> GeneratePackResponse:
    try:
        return revise_tts(request)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tts-rules", response_model=TtsRulesConfigResponse)
def get_tts_rules() -> TtsRulesConfigResponse:
    return TtsRulesConfigResponse(rules=load_system_tts_rules(), path=get_settings().tts_rules_path)


@router.put("/tts-rules", response_model=TtsRulesConfigResponse)
def save_tts_rules(request: SaveTtsRulesRequest) -> TtsRulesConfigResponse:
    try:
        rules = save_system_tts_rules(request.rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TtsRulesConfigResponse(rules=rules, path=get_settings().tts_rules_path)


@router.get("/test-gemini")
def test_gemini() -> dict:
    try:
        result = GeminiClient().test_connection()
        return {"ok": True, "result": result}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
