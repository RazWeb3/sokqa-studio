from fastapi import APIRouter, HTTPException

from app.schemas.request import PlanPackRequest, PlanSuggestConditionsRequest, PlanSuggestConditionsResponse
from app.schemas.sokqa import CoursePlan
from app.services.pack_agent import plan_pack
from app.services.plan_condition_suggester import suggest_conditions


router = APIRouter(tags=["planning"])


@router.post("/plan-pack", response_model=CoursePlan)
def create_plan(request: PlanPackRequest) -> CoursePlan:
    return plan_pack(request)


@router.post("/api/plan-suggest-conditions", response_model=PlanSuggestConditionsResponse)
def suggest_plan_conditions(request: PlanSuggestConditionsRequest) -> PlanSuggestConditionsResponse:
    try:
        return PlanSuggestConditionsResponse(suggestions=suggest_conditions(request))
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
