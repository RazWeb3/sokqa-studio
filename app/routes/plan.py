from fastapi import APIRouter

from app.schemas.request import PlanPackRequest
from app.schemas.sokqa import CoursePlan
from app.services.pack_agent import plan_pack


router = APIRouter(tags=["planning"])


@router.post("/plan-pack", response_model=CoursePlan)
def create_plan(request: PlanPackRequest) -> CoursePlan:
    return plan_pack(request)
